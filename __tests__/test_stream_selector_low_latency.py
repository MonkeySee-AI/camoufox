from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "stream-selector-low-latency.py"
SPEC = importlib.util.spec_from_file_location("stream_selector_low_latency", SCRIPT)
assert SPEC and SPEC.loader
STREAM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STREAM)


def mp4_box(kind: bytes, body: bytes, *, extended: bool = False) -> bytes:
    if extended:
        return b"\0\0\0\1" + kind + (16 + len(body)).to_bytes(8, "big") + body
    return (8 + len(body)).to_bytes(4, "big") + kind + body


def test_read_mp4_box_handles_regular_and_extended_headers() -> None:
    # FFmpeg can use either MP4 size header; preserving the original bytes lets
    # the HTTP path forward boxes without remuxing or copying their payloads.
    regular = mp4_box(b"ftyp", b"abc")
    extended = mp4_box(b"mdat", b"payload", extended=True)
    source = io.BytesIO(regular + extended)

    assert STREAM.read_mp4_box(source) == (b"ftyp", regular)
    assert STREAM.read_mp4_box(source) == (b"mdat", extended)
    assert STREAM.read_mp4_box(source) is None


def test_read_mp4_box_rejects_truncated_payload() -> None:
    # A partial encoder write must close the stream rather than forwarding a
    # malformed media segment that permanently poisons the browser SourceBuffer.
    with pytest.raises(EOFError, match="truncated"):
        STREAM.read_mp4_box(io.BytesIO((20).to_bytes(4, "big") + b"mdat" + b"short"))


def test_fragment_stream_preserves_encoded_order() -> None:
    # Source images may be dropped before encoding, but dependent H.264 frames
    # must remain ordered once the encoder has emitted them.
    fragments = STREAM.FragmentStream()
    fragments.update(b"one")
    fragments.update(b"two")

    sequence, first = fragments.wait_for_next(0)
    next_sequence, second = fragments.wait_for_next(sequence)

    assert (sequence, first) == (1, b"one")
    assert (next_sequence, second) == (2, b"two")


def test_fit_element_frame_centers_and_flattens_alpha() -> None:
    # The codec canvas stays fixed across selector resizes and H.264 receives
    # opaque RGB pixels even when the native element paint contains alpha.
    source = STREAM.Image.new("RGBA", (2, 1), (255, 0, 0, 128))
    encoded = io.BytesIO()
    source.save(encoded, "PNG")

    result = STREAM.Image.frombytes(
        "RGB", (4, 4), STREAM.fit_element_frame(encoded.getvalue(), 4, 4, "0000ff")
    )

    assert result.getpixel((0, 0)) == (0, 0, 255)
    assert result.getpixel((1, 1)) == (128, 0, 127)
    assert result.getpixel((2, 1)) == (128, 0, 127)
    assert result.getpixel((2, 2)) == (0, 0, 255)


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
