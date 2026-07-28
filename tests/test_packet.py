from __future__ import annotations

from engine import (
    chunk_payload,
    find_sni_mid_offset,
    fragment_chunks,
    is_tls_client_hello,
    split_payload,
)

TLS_HELLO = bytes([0x16, 0x03, 0x01, 0x00, 0x05, 0x01, 0x00, 0x00, 0x01])


def test_is_tls_client_hello() -> None:
    assert is_tls_client_hello(TLS_HELLO)
    assert not is_tls_client_hello(b"\x16\x03\x01")
    assert not is_tls_client_hello(b"")


def test_split_payload() -> None:
    data = b"abcdefghij"
    left, right = split_payload(data, 4)
    assert left == b"abcd"
    assert right == b"efghij"


def test_chunk_payload_minimum_two_chunks() -> None:
    data = b"abcdefgh"
    chunks = chunk_payload(data, 100)
    assert len(chunks) >= 2
    assert b"".join(chunks) == data


def test_fragment_chunks_modes() -> None:
    data = b"0123456789"
    offset_chunks = fragment_chunks(data, "offset", 3, 20)
    size_chunks = fragment_chunks(data, "packet_size", 3, 4)
    assert len(offset_chunks) == 2
    assert b"".join(offset_chunks) == data
    assert len(size_chunks) >= 2
    assert b"".join(size_chunks) == data


def test_find_sni_mid_offset_too_short() -> None:
    assert find_sni_mid_offset(b"\x16\x03\x01") is None
    assert find_sni_mid_offset(b"") is None


def test_find_sni_mid_offset_real_hello() -> None:
    # Minimal synthetic ClientHello with SNI "example.com" (11 bytes)
    # Structure: record(5) + handshake_hdr(4) + version(2) + random(32)
    #            + session_id_len(1) + cipher_suites(4) + compression(2)
    #            + extensions_len(2) + SNI ext(type=0,len=16,list_len=14,type=0,name_len=11,"example.com")
    session_id = b"\x00"
    cipher_suites = b"\x00\x02\x00\x2f"
    compression = b"\x01\x00"
    sni_name = b"example.com"
    sni_ext = (
        b"\x00\x00"                          # type = SNI (0)
        + (len(sni_name) + 5).to_bytes(2, "big")  # ext data len
        + (len(sni_name) + 3).to_bytes(2, "big")  # list len
        + b"\x00"                            # name type = host_name
        + len(sni_name).to_bytes(2, "big")  # name len
        + sni_name
    )
    extensions = (len(sni_ext)).to_bytes(2, "big") + sni_ext
    handshake_body = (
        b"\x03\x03"           # client version
        + b"\x00" * 32        # random
        + session_id
        + cipher_suites
        + compression
        + extensions
    )
    handshake = b"\x01" + len(handshake_body).to_bytes(3, "big") + handshake_body
    record = b"\x16\x03\x01" + len(handshake).to_bytes(2, "big") + handshake

    mid = find_sni_mid_offset(record)
    assert mid is not None
    # mid should be strictly inside the SNI hostname bytes
    sni_start = record.index(sni_name)
    sni_end = sni_start + len(sni_name)
    assert sni_start < mid < sni_end, f"mid={mid} not inside SNI [{sni_start},{sni_end})"
