from __future__ import annotations

import asyncio
import importlib.util
import io
from pathlib import Path

from PIL import Image
from playwright.async_api import Page

SCRIPT = Path(__file__).parents[3] / "scripts" / "stream-juggler-screencast.py"
SPEC = importlib.util.spec_from_file_location("stream_juggler_screencast", SCRIPT)
assert SPEC and SPEC.loader
STREAM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STREAM)


async def test_element_stream_tracks_resizing_offscreen_dom_target(page: Page) -> None:
    # The real Juggler path must capture document-space pixels without scrolling
    # and publish each new native size through Playwright's screencast channel.
    await page.set_content(
        """
        <style>body { margin: 0; height: 2400px }</style>
        <div id="target" style="position:absolute;top:1400px;width:80px;height:40px;background:rgb(210,30,30)"></div>
        """
    )
    frames: list[bytes] = []
    first_frame = asyncio.Event()

    def on_frame(frame: dict[str, object]) -> None:
        frames.append(STREAM.normalize_frame_data(frame["data"]))
        first_frame.set()

    await STREAM.start_screencast(
        page,
        on_frame,
        quality=91,
        size=None,
        selector="#target",
        fps=25,
    )
    try:
        await asyncio.wait_for(first_frame.wait(), timeout=5)
        await page.evaluate(
            """
            () => {
              const target = document.querySelector('#target');
              const sizes = [[120, 55], [160, 70], [95, 45]];
              let index = 0;
              window.__resizeTimer = setInterval(() => {
                const [width, height] = sizes[index++ % sizes.length];
                target.style.width = width + 'px';
                target.style.height = height + 'px';
              }, 160);
            }
            """
        )

        # Wait for three distinct JPEG geometries so the assertion observes
        # repeated browser-side box reads, not just startup plumbing.
        deadline = asyncio.get_running_loop().time() + 5
        while len(
            {
                (size["width"], size["height"])
                for frame in frames
                if (size := STREAM.jpeg_size(frame))
            }
        ) < 3:
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("timed out waiting for resized element frames")
            await asyncio.sleep(0.05)
    finally:
        await page.evaluate("() => clearInterval(window.__resizeTimer)")
        await page.screencast.stop()

    assert await page.evaluate("() => scrollY") == 0
    assert await page.locator("#target").evaluate(
        "el => el.getBoundingClientRect().top >= innerHeight"
    )
    captured_sizes = {
        (size["width"], size["height"])
        for size in map(STREAM.jpeg_size, frames)
        if size
    }
    assert captured_sizes >= {
        (80, 40),
        (120, 55),
        (160, 70),
    }
    for frame in frames[:3]:
        image = Image.open(io.BytesIO(frame)).convert("RGB")
        red, green, blue = image.getpixel((5, 5))
        assert red > 150 and green < 100 and blue < 100
