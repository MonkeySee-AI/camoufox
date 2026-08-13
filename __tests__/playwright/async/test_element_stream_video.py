from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import shutil
from pathlib import Path

import pytest
from playwright.async_api import Page, Playwright

ROOT = Path(__file__).parents[3]


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = load_script("stream_juggler_screencast", "stream-juggler-screencast.py")
VIDEO = load_script("stream_selector_low_latency", "stream-selector-low-latency.py")


async def test_low_latency_selector_video_decodes_in_real_browser(
    page: Page, playwright: Playwright
) -> None:
    # This crosses every runtime boundary in the POC: native selector paint,
    # FFmpeg video encoding, chunked HTTP, MSE append, and browser video decode.
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("system FFmpeg is not installed")
    frame_source = CORE.LatestFrame()
    fragments = VIDEO.FragmentStream()
    _, codec = VIDEO.h264_level(320, 180)
    video = VIDEO.NativeVideoMuxer(
        ffmpeg=ffmpeg,
        frame_source=frame_source,
        fragments=fragments,
        fps=30,
        codec=codec,
    )
    server = VIDEO.start_viewer_server("127.0.0.1", 0, fragments, video.codec)
    try:
        chrome = await playwright.chromium.launch(channel="chrome", headless=True)
    except Exception as error:
        pytest.skip(f"Chrome is unavailable for H.264 POC verification: {error}")
    viewer = await chrome.new_page()

    try:
        # A changing translucent element ensures native paint and platform
        # encoding feed the live stream rather than a synthetic fixture.
        await page.set_content(
            """
            <style>
              html,body { margin: 0; background: red }
              #target {
                width: 160px; height: 90px; color: white;
                background: rgba(0, 100, 255, .8);
              }
            </style>
            <div id="target">frame <span>0</span></div>
            <script>setInterval(() => document.querySelector('span').textContent++, 30)</script>
            """
        )

        def on_frame(frame: dict[str, object]) -> None:
            frame_source.update(CORE.normalize_frame_data(frame["data"]))

        await CORE.start_screencast(
            page,
            on_frame,
            quality=90,
            size={"width": 320, "height": 180},
            selector="#target",
            fps=30,
            video=True,
            bitrate=2_000_000,
        )
        host, port = server.server_address[:2]
        await viewer.goto(f"http://{host}:{port}/")
        supported = await viewer.evaluate(
            f"MediaSource.isTypeSupported('video/mp4; codecs=\"{video.codec}\"')"
        )
        if not supported:
            pytest.skip(f"browser does not support {video.codec} through MSE")

        # Decoded frames and the fixed dimensions prove actual video playback;
        # neither a mocked encoder nor a successfully loaded HTML shell can pass.
        await viewer.wait_for_function(
            """() => window.__decodedFrames >= 10 &&
                     document.querySelector('video').videoWidth === 320 &&
                     document.querySelector('video').videoHeight === 180""",
            timeout=15_000,
        )
    finally:
        with contextlib.suppress(Exception):
            await page.screencast.stop()
        frame_source.close()
        video.close()
        server.shutdown()
        server.server_close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(chrome.close(), timeout=5)
