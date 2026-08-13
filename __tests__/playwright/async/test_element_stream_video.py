from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
from pathlib import Path

import pytest
from PIL import Image
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
    # native encoding, chunked HTTP, WebCodecs, and browser canvas presentation.
    packets = VIDEO.NativePacketStream()
    _, codec = VIDEO.h264_level(320, 180)
    server = VIDEO.start_viewer_server("127.0.0.1", 0, packets, codec)
    try:
        chrome = await playwright.chromium.launch(channel="chrome", headless=True)
    except Exception as error:
        pytest.skip(f"Chrome is unavailable for H.264 POC verification: {error}")
    viewer = await chrome.new_page(viewport={"width": 320, "height": 180})

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
            packets.update(CORE.normalize_frame_data(frame["data"]))

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
            f"VideoDecoder.isConfigSupported({{codec: '{codec}'}}).then(r => r.supported)"
        )
        if not supported:
            pytest.skip(f"browser does not support {codec} through WebCodecs")

        # Decoded frames and the fixed dimensions prove actual video playback;
        # neither a mocked encoder nor a successfully loaded HTML shell can pass.
        await viewer.wait_for_function(
            """() => window.__decodedFrames >= 10 &&
                     window.__streamWidth === 320 &&
                     window.__streamHeight === 180""",
            timeout=15_000,
        )

        # With a one-to-one client canvas, the native 160x90 element must stay
        # 160x90 instead of being enlarged to fill the 320x180 video frame.
        screenshot = Image.open(io.BytesIO(await viewer.screenshot())).convert("RGB")
        blue = [
            (x, y)
            for y in range(screenshot.height)
            for x in range(screenshot.width)
            if (pixel := screenshot.getpixel((x, y)))[2] > 140
            and pixel[1] > 40
            and pixel[0] < 80
        ]
        left, top = map(min, zip(*blue))
        right, bottom = map(max, zip(*blue))
        assert 150 <= right - left + 1 <= 170
        assert 82 <= bottom - top + 1 <= 98
    finally:
        with contextlib.suppress(Exception):
            await page.screencast.stop()
        packets.close()
        server.shutdown()
        server.server_close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(chrome.close(), timeout=5)
