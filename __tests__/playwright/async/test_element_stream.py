from __future__ import annotations

import asyncio
import io

from PIL import Image
from playwright.async_api import Page
from rotunda.screencast import image_size, normalize_frame_data, start_screencast
from tests.server import Server


async def capture_element_frame(page: Page, selector: str) -> Image.Image:
    frames: list[bytes] = []
    first_frame = asyncio.Event()

    def on_frame(frame: dict[str, object]) -> None:
        data = normalize_frame_data(frame["data"])
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            frames.append(data)
            first_frame.set()

    await start_screencast(
        page, on_frame, quality=91, size=None, selector=selector, fps=25
    )
    try:
        await asyncio.wait_for(first_frame.wait(), timeout=5)
    finally:
        await page.screencast.stop()
    image = Image.open(io.BytesIO(frames[0])).convert("RGBA")
    image.load()
    return image


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
        frames.append(normalize_frame_data(frame["data"]))
        first_frame.set()

    await start_screencast(
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

        # Wait for three distinct PNG geometries so the assertion observes
        # repeated browser-side box reads, not just startup plumbing.
        deadline = asyncio.get_running_loop().time() + 5
        while (
            len(
                {
                    (size["width"], size["height"])
                    for frame in frames
                    if (size := image_size(frame))
                }
            )
            < 3
        ):
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
        for size in map(image_size, frames)
        if size
    }
    assert captured_sizes >= {
        (80, 40),
        (120, 55),
        (160, 70),
    }
    for frame in frames[:3]:
        image = Image.open(io.BytesIO(frame)).convert("RGBA")
        red, green, blue, alpha = image.getpixel((5, 5))
        assert red > 150 and green < 100 and blue < 100
        assert alpha == 255


async def test_native_element_stream_excludes_pixels_below_transparency(
    page: Page,
) -> None:
    # A rounded translucent target over a red page must retain its own alpha;
    # the red backdrop must not be flattened into the native element image.
    await page.set_content(
        """
        <style>
          html, body { margin: 0; background: rgb(240, 0, 0) }
          #target {
            width: 100px; height: 80px; border-radius: 20px;
            background: rgba(0, 80, 255, .5);
          }
        </style>
        <div id="target"></div>
        """
    )
    image = await capture_element_frame(page, "#target")

    # Transparent corners and a translucent blue center prove that no page
    # pixels survived underneath the selected subtree.
    assert image.size == (100, 80)
    assert image.getpixel((0, 0))[3] == 0
    red, green, blue, alpha = image.getpixel((50, 40))
    assert red < 10 and green > 60 and blue > 240
    assert 120 <= alpha <= 130


async def test_native_element_stream_renders_backdrop_filter_on_transparency(
    page: Page,
) -> None:
    # Backdrop filtering has no portable foreground representation, so the
    # isolated render deliberately filters transparency, not the striped page.
    await page.set_content(
        """
        <style>
          html, body {
            margin: 0;
            background: repeating-linear-gradient(90deg, red 0 10px, blue 10px 20px);
          }
          #target {
            width: 90px; height: 70px; border-radius: 10px;
            background: rgba(255, 255, 255, .25);
            backdrop-filter: blur(8px);
          }
        </style>
        <div id="target"></div>
        """
    )
    image = await capture_element_frame(page, "#target")

    # Only the element's neutral translucent fill remains; neither red nor
    # blue backdrop stripes may leak into the result.
    red, green, blue, alpha = image.getpixel((45, 35))
    assert max(red, green, blue) - min(red, green, blue) <= 2
    assert 60 <= alpha <= 70


async def test_native_element_stream_isolates_mix_blend_mode(page: Page) -> None:
    # Multiplying blue against the yellow page would produce a dark composite;
    # an element-root paint instead treats transparency as its blend backdrop.
    await page.set_content(
        """
        <style>
          html, body { margin: 0; background: rgb(255, 255, 0) }
          #target {
            width: 80px; height: 60px;
            background: rgb(0, 100, 255);
            mix-blend-mode: multiply;
          }
        </style>
        <div id="target"></div>
        """
    )
    image = await capture_element_frame(page, "#target")

    red, green, blue, alpha = image.getpixel((40, 30))
    assert red < 10 and 90 <= green <= 110 and blue > 245
    assert alpha == 255


async def test_native_element_stream_blends_descendant_inside_selected_subtree(
    page: Page,
) -> None:
    # Blend modes are isolated from the page but must still blend descendants
    # against pixels painted by the selected ancestor.
    await page.set_content(
        """
        <style>
          html, body { margin: 0; background: rgb(255, 0, 0) }
          #target { width: 80px; height: 60px; background: rgb(255, 255, 0) }
          #child {
            width: 50px; height: 40px; background: rgb(0, 100, 255);
            mix-blend-mode: multiply;
          }
        </style>
        <div id="target"><div id="child"></div></div>
        """
    )
    image = await capture_element_frame(page, "#target")

    red, green, blue, alpha = image.getpixel((25, 20))
    assert red < 10 and 90 <= green <= 110 and blue < 10 and alpha == 255


async def test_native_element_stream_includes_shadow_without_page_pixels(
    page: Page,
) -> None:
    # Ink overflow belongs to the selected element, so native bounds must grow
    # for its shadow while the red document behind that overflow stays absent.
    await page.set_content(
        """
        <style>
          html, body { margin: 0; background: rgb(255, 0, 0) }
          #target {
            width: 40px; height: 40px; background: rgb(0, 220, 40);
            box-shadow:
              -8px -6px 0 4px rgba(255, 255, 0, .5),
              12px 8px 0 4px rgba(0, 0, 255, .75);
          }
        </style>
        <div id="target"></div>
        """
    )
    image = await capture_element_frame(page, "#target")
    pixels = list(image.getdata())

    # Both the box and shadow are present, and no opaque red page pixels were
    # admitted merely because the shadow expanded the capture rectangle.
    assert image.width > 60 and image.height > 50
    assert any(g > 180 and r < 20 and b < 80 and a == 255 for r, g, b, a in pixels)
    assert any(
        r > 220 and g > 220 and b < 20 and 120 <= a <= 130 for r, g, b, a in pixels
    )
    assert any(
        b > 220 and r < 20 and g < 20 and 180 <= a <= 200 for r, g, b, a in pixels
    )
    assert not any(r > 220 and g < 20 and b < 20 and a > 0 for r, g, b, a in pixels)


async def test_native_element_stream_resolves_cross_process_iframe(
    page: Page, server: Server
) -> None:
    # The parent is served from localhost and the child from the fixture's
    # cross-process 127.0.0.1 origin, forcing its pixels through Fission IPC.
    child = b"""
      <style>html,body{margin:0;background:transparent}#remote{margin:10px;width:30px;height:20px;background:rgb(0,230,70)}</style>
      <div id="remote"></div>
    """
    parent = f"""
      <style>
        html, body {{ margin: 0; background: rgb(255, 0, 0) }}
        #target {{ display: inline-block; padding: 6px; background: rgba(0, 0, 255, .5) }}
        iframe {{ display: block; width: 90px; height: 60px; border: 0 }}
      </style>
      <div id="target"><iframe src="{server.CROSS_PROCESS_PREFIX}/element-stream-child.html"></iframe></div>
    """.encode()
    server.set_route(
        "/element-stream-child.html",
        lambda request: (request.write(child), request.finish()),
    )
    server.set_route(
        "/element-stream-parent.html",
        lambda request: (request.write(parent), request.finish()),
    )
    await page.goto(server.PREFIX + "/element-stream-parent.html")
    await page.locator("iframe").wait_for(state="visible")
    await page.frames[1].wait_for_load_state()
    image = await capture_element_frame(page, "#target")

    # Native dependency resolution must supply both opaque and transparent
    # remote pixels without flattening them against the parent page.
    assert image.size == (102, 72)
    red, green, blue, alpha = image.getpixel((20, 20))
    assert red < 10 and green > 220 and blue < 90 and alpha == 255
    red, green, blue, alpha = image.getpixel((51, 36))
    assert red < 10 and green < 10 and blue > 245 and 120 <= alpha <= 130
