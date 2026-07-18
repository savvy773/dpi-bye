"""DPI Fragment Bypass — WinDivert engine."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import pydivert

FILTER = "outbound and tcp.DstPort == 443 and tcp.PayloadLength > 0"


def is_tls_client_hello(payload: bytes) -> bool:
    if len(payload) < 6:
        return False
    return payload[0] == 0x16 and payload[5] == 0x01


def split_payload(payload: bytes, offset: int) -> tuple[bytes, bytes]:
    offset = max(1, min(offset, len(payload) - 1))
    return payload[:offset], payload[offset:]


def chunk_payload(payload: bytes, size: int) -> list[bytes]:
    if len(payload) < 2:
        return [payload]
    size = max(1, min(size, len(payload) - 1))
    chunks = [payload[i : i + size] for i in range(0, len(payload), size)]
    if len(chunks) < 2:
        mid = max(1, len(payload) - 1)
        return [payload[:mid], payload[mid:]]
    return chunks


def find_sni_mid_offset(payload: bytes) -> int | None:
    """Return an offset that splits the SNI hostname in half inside payload.

    Parses a TLS ClientHello to locate the SNI extension hostname, then
    returns an offset pointing to the midpoint of that hostname so no single
    TCP segment carries the complete SNI.  Returns None if parsing fails.
    """
    try:
        # Skip: record header(5) + handshake header(4) + client version(2) + random(32)
        pos = 43
        if pos >= len(payload):
            return None
        pos += 1 + payload[pos]                                         # session ID
        if pos + 2 > len(payload):
            return None
        pos += 2 + int.from_bytes(payload[pos : pos + 2], "big")       # cipher suites
        if pos + 1 > len(payload):
            return None
        pos += 1 + payload[pos]                                         # compression
        if pos + 2 > len(payload):
            return None
        ext_end = pos + 2 + int.from_bytes(payload[pos : pos + 2], "big")
        pos += 2
        while pos + 4 <= min(ext_end, len(payload)):
            etype = int.from_bytes(payload[pos : pos + 2], "big")
            elen = int.from_bytes(payload[pos + 2 : pos + 4], "big")
            pos += 4
            if etype == 0 and pos + 5 <= len(payload):                 # SNI extension
                name_len = int.from_bytes(payload[pos + 3 : pos + 5], "big")
                sni_start = pos + 5
                sni_end = sni_start + name_len
                if sni_end <= len(payload) and name_len >= 2:
                    return sni_start + name_len // 2
                return None
            pos += elen
    except (IndexError, ValueError):
        pass
    return None


def fragment_chunks(
    payload: bytes, mode: str, split_offset: int, packet_size: int
) -> list[bytes]:
    if mode == "packet_size":
        return chunk_payload(payload, packet_size)
    # Prefer splitting at the SNI midpoint so no segment carries the full hostname.
    sni_mid = find_sni_mid_offset(payload)
    effective = sni_mid if (sni_mid and 1 < sni_mid < len(payload) - 1) else split_offset
    part1, part2 = split_payload(payload, effective)
    return [part1, part2]


@dataclass
class Settings:
    fragment_mode: str = "packet_size"
    split_offset: int = 4
    packet_size: int = 10
    auto_connect: bool = False


@dataclass
class Engine:
    settings: Settings = field(default_factory=Settings)
    running: bool = False
    status: str = "Disconnected"
    fragmented: int = 0
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _handle: pydivert.WinDivert | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.fragmented = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        for _ in range(30):
            if self.status in ("Connected", "Error"):
                break
            time.sleep(0.1)

    def stop(self) -> None:
        self._stop.set()
        self._close_handle()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.running = False
        self.status = "Disconnected"

    def _close_handle(self) -> None:
        with self._lock:
            if self._handle is not None:
                try:
                    if self._handle.is_open:
                        self._handle.close()
                except (OSError, RuntimeError):
                    pass
                self._handle = None

    def _run(self) -> None:
        self.running = True
        self.status = "Connecting"
        try:
            handle = pydivert.WinDivert(FILTER)
            handle.open()
        except OSError:
            self.status = "Error"
            self.running = False
            return

        with self._lock:
            self._handle = handle
        self.status = "Connected"

        try:
            while not self._stop.is_set():
                try:
                    packet = handle.recv()
                except OSError as exc:
                    if self._stop.is_set() or getattr(exc, "winerror", None) in (
                        232,
                        995,
                    ):
                        break
                    raise

                try:
                    self._process(handle, packet)
                except OSError:
                    pass
        finally:
            self._close_handle()
            self.running = False
            if self.status != "Error":
                self.status = "Disconnected"

    def _process(
        self, handle: pydivert.WinDivert, packet: pydivert.Packet
    ) -> None:
        payload = packet.payload
        tcp = packet.tcp

        if not payload or not tcp or not is_tls_client_hello(payload):
            try:
                handle.send(packet, recalculate_checksum=False)
            except OSError:
                pass
            return

        chunks = fragment_chunks(
            payload,
            self.settings.fragment_mode,
            self.settings.split_offset,
            self.settings.packet_size,
        )
        orig_seq = tcp.seq_num
        offset = 0
        for chunk in chunks:
            tcp.seq_num = orig_seq + offset
            packet.payload = chunk
            handle.send(packet, recalculate_checksum=True)
            offset += len(chunk)
        self.fragmented += 1
