from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "stream-selector-webrtc.py"
SPEC = importlib.util.spec_from_file_location("stream_selector_webrtc", SCRIPT)
assert SPEC and SPEC.loader
STREAM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STREAM)


def native_packet(data: bytes, *, keyframe: bool, pts_us: int) -> bytes:
    header = bytearray(b"RSE2")
    header.append(keyframe)
    header.extend(len(data).to_bytes(4, "big"))
    header.extend(pts_us.to_bytes(8, "big"))
    header.extend((16_666).to_bytes(4, "big"))
    for value in (3840, 2160, 0, 0, 3840, 2160):
        header.extend(value.to_bytes(4, "big"))
    return bytes(header) + data


@pytest.mark.asyncio
async def test_webrtc_bridge_resumes_on_a_keyframe_after_falling_behind() -> None:
    # A saturated live queue must discard dependent frames until the next IDR,
    # so congestion produces a jump forward rather than corrupted H.264.
    track = STREAM.NativeVideoTrack("h264", queue_size=1)
    track.push(native_packet(b"old-key", keyframe=True, pts_us=0))
    track.push(native_packet(b"delta-1", keyframe=False, pts_us=16_666))
    track.push(native_packet(b"delta-2", keyframe=False, pts_us=33_332))
    track.push(native_packet(b"new-key", keyframe=True, pts_us=49_998))

    encoded = await track.recv()

    assert bytes(encoded) == b"new-key"
    assert encoded.pts == encoded.dts == 49_998
    assert encoded.time_base == STREAM.VIDEO_TIME_BASE
    assert track.dropped == 3


def test_h265_packetizer_fragments_and_reassembles_a_nal_unit() -> None:
    # Parameter NALs are aggregated and a large slice becomes RFC 7798
    # fragmentation units that preserve the complete encoded access unit.
    vps = bytes([32 << 1, 1]) + b"vps"
    sps = bytes([33 << 1, 1]) + b"sps"
    nal = bytes([(32 << 1) | 1, 0xA5]) + bytes(range(256)) * 11
    packet = STREAM.Packet(
        b"\x00\x00\x00\x01"
        + vps
        + b"\x00\x00\x00\x01"
        + sps
        + b"\x00\x00\x00\x01"
        + nal
    )
    packet.pts = 1_000_000
    packet.time_base = STREAM.VIDEO_TIME_BASE

    payloads, timestamp = STREAM.H265Packetizer().pack(packet)

    assert timestamp == 90_000
    aggregate, *fragments = payloads
    assert all(len(payload) <= STREAM.RTP_PACKET_MAX for payload in payloads)
    assert (aggregate[0] >> 1) & 0x3F == 48
    assert (
        aggregate[2:]
        == len(vps).to_bytes(2, "big") + vps + len(sps).to_bytes(2, "big") + sps
    )
    assert all((payload[0] >> 1) & 0x3F == 49 for payload in fragments)
    assert fragments[0][2] & 0x80
    assert fragments[-1][2] & 0x40
    assert nal == nal[:2] + b"".join(payload[3:] for payload in fragments)
    assert 49 in STREAM.aiortc_rtp.DYNAMIC_PAYLOAD_TYPES
