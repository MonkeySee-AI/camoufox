from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "stream-selector-low-latency.py"
SPEC = importlib.util.spec_from_file_location("stream_selector_low_latency", SCRIPT)
assert SPEC and SPEC.loader
STREAM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STREAM)


def test_native_packet_stream_preserves_encoded_order() -> None:
    # Source images may be dropped before encoding, but dependent H.264 frames
    # must remain ordered once the encoder has emitted them.
    packets = STREAM.NativePacketStream()
    packets.update(b"one")
    packets.update(b"two")

    sequence, first = packets.wait_for_next(0)
    next_sequence, second = packets.wait_for_next(sequence)

    assert (sequence, first) == (1, b"one")
    assert (next_sequence, second) == (2, b"two")


def native_packet(*frames: bytes) -> bytes:
    packet = bytearray()
    for index, frame in enumerate(frames):
        packet.extend(b"RSE2")
        packet.append(index == 0)
        packet.extend(len(frame).to_bytes(4, "big"))
        packet.extend((index * 16_666).to_bytes(8, "big"))
        packet.extend((16_666).to_bytes(4, "big"))
        packet.extend((1280).to_bytes(4, "big"))
        packet.extend((720).to_bytes(4, "big"))
        packet.extend((480).to_bytes(4, "big"))
        packet.extend((270).to_bytes(4, "big"))
        packet.extend((320).to_bytes(4, "big"))
        packet.extend((180).to_bytes(4, "big"))
        packet.extend(frame)
    return bytes(packet)


def test_parse_native_frames_preserves_annex_b_payloads() -> None:
    # The Python side relays only compressed NAL units; all paint, resize,
    # alpha flattening, and encode work must have happened inside Gecko.
    frames = [b"\0\0\0\1gSPS", b"\0\0\0\1eIDR"]

    assert STREAM.parse_native_frames(native_packet(*frames)) == frames


def test_parse_native_frames_rejects_truncated_payload() -> None:
    with pytest.raises(ValueError, match="truncated"):
        STREAM.parse_native_frames(native_packet(b"frame")[:-1])


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((1280, 720), ("3.2", "avc1.42C020")),
        ((1920, 1080), ("4.2", "avc1.42C02A")),
        ((3840, 2160), ("5.2", "avc1.42C034")),
    ],
)
def test_h264_level_matches_fixed_encoder_canvas(
    size: tuple[int, int], expected: tuple[str, str]
) -> None:
    assert STREAM.h264_level(*size) == expected


def test_h265_uses_hevc_annex_b_and_4k60_webcodecs_codec() -> None:
    # The macOS 4K60 path uses HEVC Annex B directly in Chrome WebCodecs.
    assert STREAM.web_codec("h265", 3840, 2160) == "hvc1.1.6.L156.B0"
