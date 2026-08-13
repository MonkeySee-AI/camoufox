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
            <script>
              setInterval(() => document.querySelector('span').textContent++, 30);
              let large = false;
              setInterval(() => {
                large = !large;
                const target = document.querySelector('#target');
                target.style.width = (large ? 200 : 160) + 'px';
                target.style.height = (large ? 110 : 90) + 'px';
              }, 250);
            </script>
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
        await viewer.evaluate(
            """() => {
              window.__observedContentSizes = [];
              new ResizeObserver(([entry]) => {
                const {width, height} = entry.contentRect;
                window.__observedContentSizes.push([width, height]);
              }).observe(document.querySelector('canvas'));
            }"""
        )
        supported = await viewer.evaluate(
            f"VideoDecoder.isConfigSupported({{codec: '{codec}'}}).then(r => r.supported)"
        )
        if not supported:
            pytest.skip(f"browser does not support {codec} through WebCodecs")

        # Decoded frames, fixed stream dimensions, and two observed canvas sizes
        # prove that real crop metadata shrink-wraps the changing DOM element.
        try:
            await viewer.wait_for_function(
                """() => window.__decodedFrames >= 10 &&
                         window.__streamWidth === 320 &&
                         window.__streamHeight === 180 &&
                         window.__observedContentSizes.some(([w, h]) => Math.abs(w-160) <= 1 && Math.abs(h-90) <= 1) &&
                         window.__observedContentSizes.some(([w, h]) => Math.abs(w-200) <= 1 && Math.abs(h-110) <= 1)""",
                timeout=15_000,
            )
        except Exception as error:
            state = await viewer.evaluate(
                """() => ({
                  decoded: window.__decodedFrames,
                  error: window.__decoderError,
                  stream: [window.__streamWidth, window.__streamHeight],
                  content: [window.__contentWidth, window.__contentHeight],
                  observed: window.__observedContentSizes,
                })"""
            )
            raise AssertionError(f"selector crop did not resize: {state}") from error

        # The current crop contains element pixels rather than an empty canvas;
        # its geometry must be one of the two source sizes, not the video size.
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
        painted_size = (right - left + 1, bottom - top + 1)
        assert 100 <= painted_size[0] <= 220, painted_size
        assert 50 <= painted_size[1] <= 130, painted_size
    finally:
        with contextlib.suppress(Exception):
            await page.screencast.stop()
        packets.close()
        server.shutdown()
        server.server_close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(chrome.close(), timeout=5)
