from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
from pathlib import Path

import pytest
from playwright.async_api import Page, Playwright
from rotunda.screencast import normalize_frame_data, start_video_stream, stop_video_stream
from tests.server import Server

ROOT = Path(__file__).parents[3]


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The WebRTC bridge and viewer remain script-only; the tests drive the same
# client that production embeds. The bridge unit tests live here too so they
# run where the aiortc/aiohttp dependency group is installed.
WEBRTC = load_script("stream_selector_webrtc", "stream-selector-webrtc.py")


def native_packet(data: bytes, *, keyframe: bool, pts_us: int) -> bytes:
    header = bytearray(b"RSE2")
    header.append(keyframe)
    header.extend(len(data).to_bytes(4, "big"))
    header.extend(pts_us.to_bytes(8, "big"))
    header.extend((16_666).to_bytes(4, "big"))
    for value in (3840, 2160, 0, 0, 3840, 2160):
        header.extend(value.to_bytes(4, "big"))
    return bytes(header) + data


async def test_webrtc_bridge_resumes_on_a_keyframe_after_falling_behind() -> None:
    # A saturated live queue must discard dependent frames until the next IDR,
    # so congestion produces a jump forward rather than corrupted H.264.
    track = WEBRTC.NativeVideoTrack("h264", queue_size=1)
    track.push(native_packet(b"old-key", keyframe=True, pts_us=0))
    track.push(native_packet(b"delta-1", keyframe=False, pts_us=16_666))
    track.push(native_packet(b"delta-2", keyframe=False, pts_us=33_332))
    track.push(native_packet(b"new-key", keyframe=True, pts_us=49_998))

    encoded = await track.recv()

    assert bytes(encoded) == b"new-key"
    assert encoded.pts == encoded.dts == 49_998
    assert encoded.time_base == WEBRTC.VIDEO_TIME_BASE
    assert track.dropped == 3


def test_h265_packetizer_fragments_and_reassembles_a_nal_unit() -> None:
    # Parameter NALs are aggregated and a large slice becomes RFC 7798
    # fragmentation units that preserve the complete encoded access unit.
    vps = bytes([32 << 1, 1]) + b"vps"
    sps = bytes([33 << 1, 1]) + b"sps"
    nal = bytes([(32 << 1) | 1, 0xA5]) + bytes(range(256)) * 11
    packet = WEBRTC.Packet(
        b"\x00\x00\x00\x01"
        + vps
        + b"\x00\x00\x00\x01"
        + sps
        + b"\x00\x00\x00\x01"
        + nal
    )
    packet.pts = 1_000_000
    packet.time_base = WEBRTC.VIDEO_TIME_BASE

    payloads, timestamp = WEBRTC.H265Packetizer().pack(packet)

    assert timestamp == 90_000
    aggregate, *fragments = payloads
    assert all(len(payload) <= WEBRTC.RTP_PACKET_MAX for payload in payloads)
    assert (aggregate[0] >> 1) & 0x3F == 48
    assert (
        aggregate[2:]
        == len(vps).to_bytes(2, "big") + vps + len(sps).to_bytes(2, "big") + sps
    )
    assert all((payload[0] >> 1) & 0x3F == 49 for payload in fragments)
    assert fragments[0][2] & 0x80
    assert fragments[-1][2] & 0x40
    assert nal == nal[:2] + b"".join(payload[3:] for payload in fragments)
    assert 49 in WEBRTC.aiortc_rtp.DYNAMIC_PAYLOAD_TYPES

SAMPLE_VIDEO_PIXEL = """([x, y]) => {
  const video = document.querySelector('video');
  const canvas = new OffscreenCanvas(video.videoWidth, video.videoHeight);
  const context = canvas.getContext('2d');
  context.drawImage(video, 0, 0);
  return [...context.getImageData(x, y, 1, 1).data];
}"""


def bridge_args(codec: str = "h264") -> argparse.Namespace:
    return argparse.Namespace(
        codec=codec,
        jitter_buffer_ms=50,
        host="127.0.0.1",
        port=0,
        ice_server=[],
        ice_username=None,
        ice_credential=None,
        cert_file=None,
        key_file=None,
    )


@contextlib.asynccontextmanager
async def webrtc_viewer(playwright: Playwright, viewport: dict[str, int]):
    state: dict[str, object] = {
        "offer_lock": asyncio.Lock(),
        "pc": None,
        "track": None,
    }
    runner, viewer_url = await WEBRTC.start_server(bridge_args(), state)
    try:
        chrome = await playwright.chromium.launch(channel="chrome", headless=True)
    except Exception as error:
        await runner.cleanup()
        pytest.skip(f"Chrome is unavailable for WebRTC verification: {error}")
    viewer = await chrome.new_page(viewport=viewport)
    try:
        yield state, viewer, viewer_url
    finally:
        if pc := state.get("pc"):
            with contextlib.suppress(Exception):
                await pc.close()
        await runner.cleanup()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(chrome.close(), timeout=5)


async def test_webrtc_selector_video_crops_in_real_browser(
    page: Page, playwright: Playwright
) -> None:
    # This crosses every runtime boundary of the shipped path: native selector
    # paint, native encoding, RTP/SRTP, browser decode, and crop shrink-wrap
    # over the metadata data channel.
    async with webrtc_viewer(
        playwright, viewport={"width": 320, "height": 180}
    ) as (state, viewer, viewer_url):
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
            if track := state.get("track"):
                track.push(normalize_frame_data(frame["data"]))

        await start_video_stream(
            page,
            on_frame,
            size={"width": 320, "height": 180},
            selector="#target",
            fps=30,
            bitrate=2_000_000,
            codec="h264",
        )
        await viewer.goto(viewer_url)

        # Fixed stream dimensions plus both element sizes in the crop history
        # prove real crop metadata shrink-wraps the changing DOM element.
        try:
            await viewer.wait_for_function(
                """() => {
                  const video = document.querySelector('video');
                  const near = (crop, w, h) =>
                    Math.abs(crop.width - w) <= 1 && Math.abs(crop.height - h) <= 1;
                  return (window.__webrtc.framesDecoded || 0) >= 10 &&
                         video.videoWidth === 320 && video.videoHeight === 180 &&
                         window.__cropHistory.some(crop => near(crop, 160, 90)) &&
                         window.__cropHistory.some(crop => near(crop, 200, 110));
                }""",
                timeout=15_000,
            )
        except Exception as error:
            diagnostics = await viewer.evaluate(
                """() => ({
                  stats: window.__webrtc,
                  crop: window.__crop,
                  history: window.__cropHistory,
                })"""
            )
            raise AssertionError(f"selector crop did not resize: {diagnostics}") from error

        # The crop region contains element pixels, not matte: sample the
        # decoded video inside the current crop.
        crop = await viewer.evaluate("() => window.__crop")
        pixel = await viewer.evaluate(
            SAMPLE_VIDEO_PIXEL,
            [crop["x"] + crop["width"] // 2, crop["y"] + crop["height"] // 2],
        )
        assert pixel[2] > 140 and pixel[0] < 80, pixel

        with contextlib.suppress(Exception):
            await stop_video_stream(page)


async def test_webrtc_viewport_video_fills_canvas_and_resolves_iframe(
    page: Page, playwright: Playwright, server: Server
) -> None:
    # This selector-less request must paint only the visible viewport, resolve
    # a remote iframe, upscale it natively, and arrive as decodable video with
    # a full-canvas crop.
    server.set_route(
        "/viewport-video-child.html",
        lambda request: (
            request.write(b"<style>html,body{margin:0;height:100%;background:rgb(0,220,70)}</style>"),
            request.finish(),
        ),
    )
    parent = f"""
      <style>
        html, body {{ margin: 0; height: 500px; background: rgb(15, 30, 180) }}
        iframe {{ position: absolute; left: 80px; top: 40px; width: 120px; height: 80px; border: 0 }}
        #offscreen {{ position: absolute; top: 300px; width: 100px; height: 100px; background: yellow }}
      </style>
      <iframe src="{server.CROSS_PROCESS_PREFIX}/viewport-video-child.html"></iframe>
      <div id="offscreen"></div>
    """.encode()
    server.set_route(
        "/viewport-video-parent.html",
        lambda request: (request.write(parent), request.finish()),
    )
    await page.set_viewport_size({"width": 320, "height": 180})
    await page.goto(server.PREFIX + "/viewport-video-parent.html")
    await page.frames[1].wait_for_load_state()

    async with webrtc_viewer(
        playwright, viewport={"width": 640, "height": 360}
    ) as (state, viewer, viewer_url):
        def on_frame(frame: dict[str, object]) -> None:
            if track := state.get("track"):
                track.push(normalize_frame_data(frame["data"]))

        await start_video_stream(
            page,
            on_frame,
            size={"width": 640, "height": 360},
            fps=30,
            bitrate=3_000_000,
            codec="h264",
        )
        await viewer.goto(viewer_url)

        # A full-canvas crop proves the 320x180 viewport was replayed at 2x
        # rather than centered at its source dimensions like an element.
        try:
            await viewer.wait_for_function(
                """() => {
                  const video = document.querySelector('video');
                  return (window.__webrtc.framesDecoded || 0) >= 10 &&
                         video.videoWidth === 640 && video.videoHeight === 360 &&
                         window.__crop && window.__crop.width === 640 &&
                         window.__crop.height === 360;
                }""",
                timeout=15_000,
            )
        except Exception as error:
            diagnostics = await viewer.evaluate(
                "() => ({stats: window.__webrtc, crop: window.__crop})"
            )
            diagnostics["source"] = await page.evaluate(
                """() => ({
                  inner: [innerWidth, innerHeight],
                  client: [document.documentElement.clientWidth,
                           document.documentElement.clientHeight],
                })"""
            )
            raise AssertionError(f"viewport video did not fill output: {diagnostics}") from error

        # Parent blue and remote-frame green must both survive the native
        # compositor snapshot; the offscreen yellow block must not appear.
        blue = await viewer.evaluate(SAMPLE_VIDEO_PIXEL, [500, 300])
        green = await viewer.evaluate(SAMPLE_VIDEO_PIXEL, [240, 160])
        left = await viewer.evaluate(SAMPLE_VIDEO_PIXEL, [50, 300])
        assert blue[2] > 120 and blue[0] < 70 and blue[1] < 80, blue
        assert green[1] > 180 and green[1] - max(green[0], green[2]) > 100, green
        assert left[2] > 120, left

        with contextlib.suppress(Exception):
            await stop_video_stream(page)
