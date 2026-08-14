# Copyright (c) 2026 Pierce Freeman.

"""Shared helpers for driving Juggler screencast streams over Playwright."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

from rotunda.async_api import AsyncNewBrowser, async_connect_over_remote_juggler


def parse_viewport(value: str) -> dict[str, int]:
    # Raises ValueError so argparse `type=` call sites report a clean usage
    # error; click call sites adapt it to BadParameter themselves.
    try:
        width, height = value.lower().split("x", 1)
        return {"width": int(width), "height": int(height)}
    except Exception as exc:
        raise ValueError("must look like 1280x720") from exc


async def resolve_page(
    playwright: Any, args: SimpleNamespace
) -> tuple[Any, Any | None]:
    if args.endpoint:
        browser = await async_connect_over_remote_juggler(playwright, args.endpoint)
        if args.new_context or not browser.contexts:
            context = await browser.new_context(viewport=args.viewport)
            page = await context.new_page()
            return browser, page
        context = browser.contexts[0]
        if context.pages and not args.new_page:
            return browser, context.pages[min(args.page_index, len(context.pages) - 1)]
        return browser, await context.new_page()

    browser = await AsyncNewBrowser(
        playwright,
        headless=args.headless,
        executable_path=args.executable_path,
        debug=args.debug,
    )
    context = await browser.new_context(viewport=args.viewport)
    return browser, await context.new_page()


def normalize_frame_data(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return base64.b64decode(data)
    raise TypeError(f"Unexpected screencast frame payload: {type(data).__name__}")


def jpeg_size(data: bytes) -> dict[str, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    offset = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset < len(data):
        while offset < len(data) and data[offset] != 0xFF:
            offset += 1
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return None
        marker = data[offset]
        offset += 1
        if marker in {0x01, *range(0xD0, 0xD8), 0xD9}:
            continue
        if offset + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            return None
        if marker in sof_markers:
            if segment_length < 7:
                return None
            return {
                "width": int.from_bytes(data[offset + 5 : offset + 7], "big"),
                "height": int.from_bytes(data[offset + 3 : offset + 5], "big"),
            }
        offset += segment_length
    return None


def image_size(data: bytes) -> dict[str, int] | None:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return {
            "width": int.from_bytes(data[16:20], "big"),
            "height": int.from_bytes(data[20:24], "big"),
        }
    return jpeg_size(data)


async def start_screencast(
    page: Any,
    on_frame: Any,
    quality: int,
    size: dict[str, int] | None,
    *,
    selector: str | None = None,
    fps: int = 25,
) -> None:
    """Start an image screencast: JPEG viewport frames, or PNG element frames
    when a selector is given. Stop with ``page.screencast.stop()``."""
    screencast = page.screencast._impl_obj
    if screencast._started:
        raise RuntimeError("Screencast is already started")
    screencast._started = True
    screencast._on_frame = on_frame
    try:
        params: dict[str, Any] = {
            "quality": quality,
            "sendFrames": True,
            "record": False,
        }
        if size:
            params["size"] = size
        if selector:
            params["selector"] = selector
            params["fps"] = fps
        await screencast._page._channel.send_return_as_dict(
            "screencastStart",
            None,
            params,
        )
    except Exception:
        screencast._started = False
        screencast._on_frame = None
        raise


async def start_video_stream(
    page: Any,
    on_frame: Any,
    *,
    size: dict[str, int] | None = None,
    selector: str | None = None,
    fps: int = 25,
    bitrate: int = 12_000_000,
    codec: str = "h264",
) -> None:
    """Start a native compressed video stream (RSE2 packets) of the viewport,
    or of one element when a selector is given. macOS only. Stop with
    :func:`stop_video_stream`."""
    screencast = page.screencast._impl_obj
    if screencast._started:
        raise RuntimeError("Screencast is already started")
    screencast._started = True
    screencast._on_frame = on_frame
    try:
        params: dict[str, Any] = {"fps": fps, "bitrate": bitrate, "codec": codec}
        if size:
            params["size"] = size
        if selector:
            params["selector"] = selector
        await screencast._page._channel.send_return_as_dict(
            "videoStreamStart",
            None,
            params,
        )
    except Exception:
        screencast._started = False
        screencast._on_frame = None
        raise


async def stop_video_stream(page: Any) -> None:
    screencast = page.screencast._impl_obj
    try:
        await screencast._page._channel.send_return_as_dict(
            "videoStreamStop",
            None,
            {},
        )
    finally:
        screencast._started = False
        screencast._on_frame = None
