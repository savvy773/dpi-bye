"""DPI Fragment Bypass — minimal TUI."""

from __future__ import annotations

import atexit
import ctypes
import importlib.util
import json
import os
import re
import signal
import sys
from ctypes import wintypes
from pathlib import Path

if importlib.util.find_spec("pydivert") is None:
    print("[!] pydivert not found — run uv sync then .\\dpi-bypass.ps1")
    sys.exit(1)

from engine import Engine, Settings

# ── paths ──

DATA_DIR = Path(".data")
SETTINGS_FILE = DATA_DIR / "user_settings.json"
MUTEX_NAME = r"Local\DpiBypass.SingleInstance"


def activate() -> None:
    os.chdir(Path(__file__).resolve().parent)
    DATA_DIR.mkdir(exist_ok=True)


def ensure_admin() -> None:
    if sys.platform != "win32":
        return
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            print("[!] Administrator rights required — run .\\dpi-bypass.ps1 as Administrator.")
            sys.exit(1)
    except Exception:
        pass


def ensure_single() -> bool:
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, wintypes.BOOL(True), MUTEX_NAME)
    return kernel32.GetLastError() != 183


# ── settings ──


def load_settings() -> Settings:
    if not SETTINGS_FILE.exists():
        return Settings()
    try:
        d = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return Settings(
            fragment_mode="offset"
            if d.get("fragment_mode") == "offset"
            else "packet_size",
            split_offset=OFFSET_PRESETS[_nearest_preset_idx(OFFSET_PRESETS, int(d.get("split_offset", 4)))],
            packet_size=SIZE_PRESETS[_nearest_preset_idx(SIZE_PRESETS, int(d.get("packet_size", 10)))],
            auto_connect=bool(d.get("auto_connect", False)),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return Settings()


def save_settings(s: Settings) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(
            {
                "fragment_mode": s.fragment_mode,
                "split_offset": s.split_offset,
                "packet_size": s.packet_size,
                "auto_connect": s.auto_connect,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


# ── console ──

RST      = "\033[0m"
BOLD     = "\033[1m"
DIM      = "\033[2m"
# pastel palette
C_CYAN   = "\033[38;5;153m"   # pastel sky-blue
C_GREEN  = "\033[38;5;157m"   # pastel mint-green
C_YELLOW = "\033[38;5;222m"   # pastel yellow
C_RED    = "\033[38;5;210m"   # pastel salmon
C_GRAY   = "\033[38;5;252m"   # light silver-gray
C_WHITE  = "\033[97m"
C_STAR   = "\033[38;5;222m"   # pastel gold for ★
C_ACCENT = "\033[38;5;147m"   # pastel lavender accent
C_ACT    = "\033[38;5;157m"   # pastel mint for ► active indicator

_ANSI = re.compile(r"\033\[[0-9;]*m")


def _vis(s: str) -> int:
    """Visible (display) length of s, ignoring ANSI escape codes."""
    return len(_ANSI.sub("", s))


def _rpad(s: str, w: int) -> str:
    """Right-pad s to visual width w."""
    return s + " " * max(0, w - _vis(s))

MENU_ITEMS = ["connect", "packet_size", "offset", "auto_connect", "quit"]

OFFSET_PRESETS = [4, 10, 20]
SIZE_PRESETS   = [5, 10, 20]
PRESET_LABELS  = ["small", "mid", "large"]


def _nearest_preset_idx(presets: list[int], value: int) -> int:
    return min(range(len(presets)), key=lambda i: abs(presets[i] - value))


def _preset_label(presets: list[int], value: int) -> str:
    idx = _nearest_preset_idx(presets, value)
    return f"{PRESET_LABELS[idx]} ({presets[idx]})"


def enable_vt() -> None:
    if sys.platform == "win32":
        try:
            k32 = ctypes.windll.kernel32
            h = k32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k32.GetConsoleMode(h, ctypes.byref(mode)):
                k32.SetConsoleMode(h, mode.value | 0x0004)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stdin, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def read_key() -> str:
    """Return a key token: 'up','down','left','right','enter','q','c', etc."""
    if sys.platform != "win32":  # type: ignore[misc]
        try:
            line = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "quit"
        return line or "enter"

    import msvcrt

    ch = msvcrt.getch()
    if ch == b"\x03":
        raise KeyboardInterrupt
    if ch in (b"\x00", b"\xe0"):
        ch2 = msvcrt.getch()
        return {
            b"H": "up",
            b"P": "down",
            b"K": "left",
            b"M": "right",
        }.get(ch2, "")
    if ch in (b"\r", b"\n"):
        return "enter"
    try:
        return ch.decode("utf-8", errors="ignore").lower()
    except Exception:
        return ""


# ── render ──

W    = 54
HBAR = f"{C_ACCENT}{'━' * W}{RST}"          # header bar — bright accent
TBAR = f"{DIM}{C_GRAY}{'─' * W}{RST}"       # thin separator bar


def cls() -> None:
    # hide cursor → go home → clear down: reduces flicker on redraw
    print("\033[?25l\033[H\033[J", end="", flush=True)


def _minimap(presets: list[int], value: int) -> str:
    idx = _nearest_preset_idx(presets, value)
    return " ".join(
        f"{C_GREEN}●{RST}" if i == idx else f"{DIM}{C_GRAY}○{RST}"
        for i in range(len(presets))
    )


_REC_SIZE_IDX = 1   # mid (10)
_REC_OFF_IDX  = 0   # small (4)

_DESC = [
    "Intercept TLS handshake · start SNI fragmentation · WinDivert filter on",
    "Smaller = stronger bypass, higher CPU  │  5 aggressive · 10 recommended★ · 20 light",
    "Manual fallback only — Connect always uses packet_size first, this preset is not auto-applied",
    "Start bypass automatically with last saved settings when the program opens",
    "Release WinDivert → restore normal internet",
]


def render(engine: Engine, sel: int) -> None:
    s = engine.settings
    cls()

    # ── Header ──
    print(f"  {BOLD}{C_ACCENT}◈  DPI Fragment Bypass{RST}")
    print(f"  {DIM}{C_GRAY}SNI split bypass  ·  KT / SKT / LGU+{RST}")
    print(HBAR)
    print()

    # ── Status ──
    # (badge background, badge text color, icon)
    status_badge = {
        "Connected":    ("\033[48;5;29m",  "\033[97m", "●"),
        "Connecting":   ("\033[48;5;136m", "\033[30m", "◌"),
        "Disconnected": ("\033[48;5;238m", "\033[97m", "○"),
        "Error":        ("\033[48;5;124m", "\033[97m", "✖"),
    }
    bg, fg, si = status_badge.get(engine.status, ("\033[48;5;238m", "\033[97m", "?"))
    frag_str = (
        f"  {DIM}{C_GRAY}fragmented {RST}{C_GREEN}{BOLD}{engine.fragmented}{RST}"
        if engine.running else ""
    )
    print(f"  {bg}{fg}{BOLD} {si} {engine.status.upper()} {RST}{frag_str}")
    print()

    # ── Menu ──
    sz_idx = _nearest_preset_idx(SIZE_PRESETS, s.packet_size)

    # (label, value, starred, minimap, hint, active-mode)
    rows: list[tuple[str, str, bool, str | None, str, bool]] = [
        ("Connect" if not engine.running else "Disconnect",
         "", False, None, "c", False),
        ("Packet size",
         _preset_label(SIZE_PRESETS, s.packet_size),
         sz_idx == _REC_SIZE_IDX, _minimap(SIZE_PRESETS, s.packet_size),
         "←→", s.fragment_mode == "packet_size"),
        ("Offset",
         _preset_label(OFFSET_PRESETS, s.split_offset),
         _nearest_preset_idx(OFFSET_PRESETS, s.split_offset) == _REC_OFF_IDX,
         _minimap(OFFSET_PRESETS, s.split_offset),
         "←→", s.fragment_mode == "offset"),
        ("Auto-connect",
         "ON" if s.auto_connect else "OFF",
         False, None, "⏎", s.auto_connect),
        ("Quit", "", False, None, "q", False),
    ]

    LPAD = 12
    VPAD = 11
    MPAD = 5

    for i, (label, val, starred, mmap, hint, active) in enumerate(rows):
        is_sel = i == sel
        ptr      = f"{C_ACCENT}▸{RST} " if is_sel else "  "
        lc       = f"{BOLD}{C_ACCENT}"   if is_sel else C_WHITE
        vc       = f"{BOLD}{C_CYAN}"     if is_sel else C_CYAN

        val_colored = f"{vc}{val}{RST}" if val else ""
        label_col   = _rpad(f"{lc}{label}{RST}", LPAD)
        val_col     = _rpad(val_colored, VPAD)
        star_col    = f" {C_STAR}★{RST}" if starred else "  "
        act_col     = f" {C_ACT}►{RST}"  if active  else "  "
        map_col     = mmap if mmap else " " * MPAD
        hint_col    = f"{DIM}{C_GRAY}[{hint}]{RST}"

        print(f"  {ptr}{label_col}  {val_col}{star_col}{act_col}  {map_col}  {hint_col}")

    # ── Description ──
    desc = _DESC[sel] if sel < len(_DESC) else ""
    print()
    print(TBAR)
    print(f"  {C_GRAY}{desc}{RST}")
    print(TBAR)
    print(f"  {DIM}{C_GRAY}↑↓ navigate   ←→ preset   Enter / key to select{RST}")

    print("\033[?25h", end="", flush=True)  # restore cursor


# ── main loop ──


def adjust(engine: Engine, sel: int, delta: int) -> None:
    s = engine.settings
    name = MENU_ITEMS[sel]
    if name == "packet_size":
        idx = _nearest_preset_idx(SIZE_PRESETS, s.packet_size)
        s.packet_size = SIZE_PRESETS[(idx + delta) % len(SIZE_PRESETS)]
        s.fragment_mode = "packet_size"
    elif name == "offset":
        idx = _nearest_preset_idx(OFFSET_PRESETS, s.split_offset)
        s.split_offset = OFFSET_PRESETS[(idx + delta) % len(OFFSET_PRESETS)]
        s.fragment_mode = "offset"
    elif name == "auto_connect":
        s.auto_connect = not s.auto_connect
    save_settings(s)


def connect_engine(engine: Engine) -> None:
    """Connect using packet_size — the primary bypass mode. Offset is a manual
    fallback only, so it never takes priority over an actual connection."""
    engine.settings.fragment_mode = "packet_size"
    save_settings(engine.settings)
    engine.start()


def activate_item(engine: Engine, sel: int) -> bool:
    name = MENU_ITEMS[sel]
    if name == "quit":
        return False
    if name == "connect":
        if engine.running:
            engine.stop()
        else:
            connect_engine(engine)
    elif name in ("packet_size", "offset"):
        adjust(engine, sel, 1)
    elif name == "auto_connect":
        engine.settings.auto_connect = not engine.settings.auto_connect
        save_settings(engine.settings)
    return True


_CTRL_HANDLER_REF: object = None  # prevent GC of ctypes callback


def _install_ctrl_handler(engine: Engine) -> None:
    """Handle terminal-window close (CTRL_CLOSE_EVENT) on Windows."""
    CTRL_CLOSE_EVENT = 2
    HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    def _handler(ctrl_type: int) -> bool:
        if ctrl_type == CTRL_CLOSE_EVENT:
            engine.stop()
        return False  # chain to default (lets Windows terminate normally)

    global _CTRL_HANDLER_REF
    _CTRL_HANDLER_REF = HandlerRoutine(_handler)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_CTRL_HANDLER_REF, True)


def main() -> None:
    activate()
    ensure_admin()
    if not ensure_single():
        print("[!] Already running — stop with .\\dpi-bypass.ps1 -Stop then retry")
        sys.exit(1)

    enable_vt()
    if sys.platform == "win32":
        try:
            ctypes.windll.kernel32.SetConsoleTitleW("DPI Fragment Bypass")
        except Exception:
            pass

    engine = Engine(settings=load_settings())

    def cleanup(*_: object) -> None:
        engine.stop()
        save_settings(engine.settings)

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    _install_ctrl_handler(engine)

    if engine.settings.auto_connect:
        connect_engine(engine)

    sel = 0
    try:
        while True:
            render(engine, sel)
            key = read_key()

            if key == "up":
                sel = max(0, sel - 1)
            elif key == "down":
                sel = min(len(MENU_ITEMS) - 1, sel + 1)
            elif key == "left":
                adjust(engine, sel, -1)
            elif key == "right":
                adjust(engine, sel, 1)
            elif key == "enter":
                if not activate_item(engine, sel):
                    break
            elif key == "c":
                if not activate_item(engine, 0):
                    break
            elif key in ("q", "quit", "exit", "0"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()

    print("\033[?25h\033[H\033[J", end="", flush=True)  # restore cursor + clear
    print(f"  {C_GREEN}✓{RST} DPI Bypass stopped — internet restored.")
    print()


if __name__ == "__main__":
    main()
