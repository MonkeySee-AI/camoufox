from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

SCRIPT = Path(__file__).parents[1] / "scripts" / "stream-juggler-screencast.py"
SPEC = importlib.util.spec_from_file_location("stream_juggler_screencast", SCRIPT)
assert SPEC and SPEC.loader
STREAM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STREAM)


def test_image_size_reads_png_dimensions() -> None:
    # Selector streams use PNG so alpha survives the multipart transport.
    header = b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (321).to_bytes(4, "big") + (
        123
    ).to_bytes(4, "big")

    assert STREAM.image_size(header) == {"width": 321, "height": 123}


class RecordingChannel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict[str, object]]] = []

    async def send_return_as_dict(
        self, method: str, timeout: object, params: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((method, timeout, params))
        return {}


async def test_element_video_serializes_native_stream_options() -> None:
    # Selector video must carry its fixed encoder canvas and bitrate through
    # Playwright once; per-frame behavior belongs to the Rotunda integration.
    channel = RecordingChannel()
    screencast = SimpleNamespace(
        _started=False,
        _on_frame=None,
        _page=SimpleNamespace(_channel=channel),
    )
    page = SimpleNamespace(screencast=SimpleNamespace(_impl_obj=screencast))
    on_frame = object()

    await STREAM.start_screencast(
        page,
        on_frame,
        91,
        {"width": 1920, "height": 1080},
        selector="#target",
        fps=37,
        video=True,
        bitrate=8_000_000,
    )

    assert channel.calls == [
        (
            "screencastStart",
            None,
            {
                "quality": 91,
                "sendFrames": True,
                "record": False,
                "size": {"width": 1920, "height": 1080},
                "selector": "#target",
                "fps": 37,
                "video": True,
                "bitrate": 8_000_000,
            },
        )
    ]
    assert screencast._started is True
    assert screencast._on_frame is on_frame


def test_selector_defaults_to_mjpeg_and_rejects_hls(monkeypatch) -> None:
    # Selector mode should work without extra format flags while refusing the
    # fixed-geometry HLS encoder that cannot preserve resized element frames.
    modes: list[str] = []

    async def record_mode(args) -> None:
        modes.append(args.mode)

    monkeypatch.setattr(STREAM, "stream", record_mode)
    runner = CliRunner()

    result = runner.invoke(STREAM.main, ["--selector", "#target"])
    rejected = runner.invoke(
        STREAM.main, ["--selector", "#target", "--mode", "hls"]
    )

    assert result.exit_code == 0
    assert modes == ["mjpeg"]
    assert rejected.exit_code == 2
    assert "element streams require MJPEG" in rejected.output
