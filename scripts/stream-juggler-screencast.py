#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import functools
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from rotunda import AsyncNewBrowser, async_connect_over_remote_juggler


class LatestFrame:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._frame: bytes | None = None
        self._sequence = 0
        self.closed = False

    def update(self, frame: bytes) -> None:
        with self._condition:
            self._frame = frame
            self._sequence += 1
            self._condition.notify_all()

    def wait_for_first(self, timeout: float | None = None) -> bytes | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._frame is None and not self.closed:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._frame

    def wait_for_next(self, sequence: int, timeout: float | None = None) -> tuple[int, bytes | None]:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._sequence == sequence and not self.closed:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    break
                self._condition.wait(remaining)
            return self._sequence, self._frame

    def latest(self) -> bytes | None:
        with self._condition:
            return self._frame

    def close(self) -> None:
        with self._condition:
            self.closed = True
            self._condition.notify_all()


class FfmpegHlsStreamer:
    def __init__(
        self,
        *,
        ffmpeg: str,
        output_dir: Path,
        frame_source: LatestFrame,
        fps: int,
        codec: str,
        crf: int,
        hls_time: float,
        hls_list_size: int,
        loglevel: str,
    ) -> None:
        self.output_dir = output_dir
        self.frame_source = frame_source
        self.fps = fps
        keyframe_interval = max(1, round(fps * hls_time))
        self._process = subprocess.Popen(  # nosec - developer script.
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                loglevel,
                "-f",
                "image2pipe",
                "-framerate",
                str(fps),
                "-vcodec",
                "mjpeg",
                "-i",
                "pipe:0",
                "-an",
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:v",
                codec,
                "-preset",
                "veryfast",
                "-tune",
                "zerolatency",
                "-crf",
                str(crf),
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(fps),
                "-g",
                str(keyframe_interval),
                "-keyint_min",
                str(keyframe_interval),
                "-sc_threshold",
                "0",
                "-f",
                "hls",
                "-hls_time",
                str(hls_time),
                "-hls_list_size",
                str(hls_list_size),
                "-hls_allow_cache",
                "0",
                "-hls_flags",
                "delete_segments+omit_endlist+independent_segments",
                str(output_dir / "stream.m3u8"),
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._stopped = threading.Event()
        self._pump_thread = threading.Thread(target=self._pump_frames, name="hls-frame-pump", daemon=True)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, name="ffmpeg-stderr", daemon=True)
        self._pump_thread.start()
        self._stderr_thread.start()

    def _pump_frames(self) -> None:
        stdin = self._process.stdin
        if stdin is None:
            return
        frame = self.frame_source.wait_for_first()
        next_tick = time.monotonic()
        while frame is not None and not self._stopped.is_set():
            if self._process.poll() is not None:
                return
            try:
                stdin.write(frame)
                stdin.flush()
            except BrokenPipeError:
                return
            latest = self.frame_source.latest()
            if latest is not None:
                frame = latest
            next_tick += 1 / self.fps
            delay = next_tick - time.monotonic()
            if delay > 0:
                self._stopped.wait(delay)
            else:
                next_tick = time.monotonic()

    def _drain_stderr(self) -> None:
        if self._process.stderr is None:
            return
        for line in iter(self._process.stderr.readline, b""):
            if line:
                print(f"[ffmpeg] {line.decode(errors='replace').rstrip()}", file=sys.stderr, flush=True)

    def close(self) -> None:
        self._stopped.set()
        if self._process.stdin is not None:
            with contextlib.suppress(BrokenPipeError, OSError):
                self._process.stdin.close()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._process.wait(timeout=5)
        if self._process.poll() is None:
            self._process.kill()


class MjpegHandler(SimpleHTTPRequestHandler):
    frame_source: LatestFrame

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path not in {"/", "/mjpeg"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if self.path == "/":
            body = b'<html><body><img src="/mjpeg"></body></html>'
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=rotunda-frame")
        self.end_headers()
        sequence = 0
        while not self.frame_source.closed:
            sequence, frame = self.frame_source.wait_for_next(sequence, timeout=5)
            if not frame:
                continue
            try:
                self.wfile.write(b"--rotunda-frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return


class HlsHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".m3u8": "application/vnd.apple.mpegurl",
        ".ts": "video/MP2T",
    }

    def log_message(self, format: str, *args: Any) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        super().end_headers()


def start_hls_server(host: str, port: int, directory: Path) -> ThreadingHTTPServer:
    handler = functools.partial(HlsHandler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, name="hls-http-server", daemon=True).start()
    return server


def clear_stale_hls_files(directory: Path) -> None:
    for path in [directory / "stream.m3u8", *directory.glob("stream*.ts")]:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def start_mjpeg_server(host: str, port: int, frame_source: LatestFrame) -> ThreadingHTTPServer:
    handler = type("RotundaMjpegHandler", (MjpegHandler,), {"frame_source": frame_source})
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, name="mjpeg-http-server", daemon=True).start()
    return server


def parse_viewport(value: str) -> dict[str, int]:
    try:
        width, height = value.lower().split("x", 1)
        return {"width": int(width), "height": int(height)}
    except Exception as exc:
        raise argparse.ArgumentTypeError("viewport must look like 1280x720") from exc


async def resolve_page(playwright: Any, args: argparse.Namespace) -> tuple[Any, Any | None]:
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


def format_size(size: dict[str, int]) -> str:
    return f"{size['width']}x{size['height']}"


def validate_capture_size(size: dict[str, int], args: argparse.Namespace) -> None:
    width = size["width"]
    height = size["height"]
    if width < 10 or width > 10000 or height < 10 or height > 10000:
        raise SystemExit(f"Invalid capture size {format_size(size)}.")
    if args.mode == "hls" and (width % 2 or height % 2):
        raise SystemExit(
            "HLS output is encoded as yuv420p H.264 and needs even dimensions. "
            f"Use an even --viewport or --capture-size instead of {format_size(size)}."
        )


async def resolve_capture_size(page: Any, args: argparse.Namespace) -> dict[str, int]:
    if args.capture_size:
        size = args.capture_size
    else:
        size = page.viewport_size
        if not size:
            size = await page.evaluate(
                "() => ({ width: window.innerWidth, height: window.innerHeight })"
            )
    size = {"width": int(size["width"]), "height": int(size["height"])}
    validate_capture_size(size, args)
    return size


async def start_screencast(page: Any, on_frame: Any, quality: int, size: dict[str, int]) -> None:
    screencast = page.screencast._impl_obj
    if screencast._started:
        raise RuntimeError("Screencast is already started")
    screencast._started = True
    screencast._on_frame = on_frame
    try:
        await screencast._page._channel.send_return_as_dict(
            "screencastStart",
            None,
            {
                "quality": quality,
                "sendFrames": True,
                "record": False,
                "size": size,
            },
        )
    except Exception:
        screencast._started = False
        screencast._on_frame = None
        raise


async def stream(args: argparse.Namespace) -> None:
    frame_source = LatestFrame()
    output_tmp: tempfile.TemporaryDirectory[str] | None = None
    output_dir: Path | None = None
    streamer: FfmpegHlsStreamer | None = None
    server: ThreadingHTTPServer | None = None

    try:
        if args.mode == "hls":
            ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
            if not ffmpeg:
                raise SystemExit("ffmpeg is required for HLS mode. Pass --ffmpeg or install ffmpeg.")
            if args.output_dir:
                output_dir = args.output_dir
                output_dir.mkdir(parents=True, exist_ok=True)
            else:
                output_tmp = tempfile.TemporaryDirectory(prefix="rotunda-hls-")
                output_dir = Path(output_tmp.name)
            clear_stale_hls_files(output_dir)
            streamer = FfmpegHlsStreamer(
                ffmpeg=ffmpeg,
                output_dir=output_dir,
                frame_source=frame_source,
                fps=args.fps,
                codec=args.codec,
                crf=args.crf,
                hls_time=args.hls_time,
                hls_list_size=args.hls_list_size,
                loglevel=args.ffmpeg_loglevel,
            )
            server = start_hls_server(args.host, args.port, output_dir)
            actual_host, actual_port = server.server_address[:2]
            print(f"HLS stream: http://{actual_host}:{actual_port}/stream.m3u8", flush=True)
            print("Open that URL in QuickTime or VLC after the first frame arrives.", flush=True)
        else:
            server = start_mjpeg_server(args.host, args.port, frame_source)
            actual_host, actual_port = server.server_address[:2]
            print(f"MJPEG stream: http://{actual_host}:{actual_port}/mjpeg", flush=True)
            print("Open that URL in VLC or a browser.", flush=True)

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)

        async with async_playwright() as playwright:
            browser, page = await resolve_page(playwright, args)
            try:
                if args.url:
                    await page.goto(args.url)
                capture_size = await resolve_capture_size(page, args)
                print(f"Requested capture size: {format_size(capture_size)}", flush=True)
                frame_count = 0
                actual_frame_size: dict[str, int] | None = None

                def on_frame(frame: dict[str, Any]) -> None:
                    nonlocal actual_frame_size, frame_count
                    frame_data = normalize_frame_data(frame["data"])
                    if actual_frame_size is None:
                        actual_frame_size = jpeg_size(frame_data)
                        if actual_frame_size:
                            print(f"Actual frame size: {format_size(actual_frame_size)}", flush=True)
                    frame_source.update(frame_data)
                    frame_count += 1
                    if args.print_frames and frame_count % args.print_frames == 0:
                        print(f"frames={frame_count}", flush=True)

                await start_screencast(page, on_frame, args.quality, capture_size)
                try:
                    if args.seed_screenshot:
                        viewport_size = page.viewport_size
                        if viewport_size == capture_size:
                            frame_source.update(
                                await page.screenshot(type="jpeg", quality=args.quality)
                            )
                    await stop_event.wait()
                finally:
                    with contextlib.suppress(Exception):
                        await page.screencast.stop()
            finally:
                await browser.close()
    finally:
        frame_source.close()
        if server:
            server.shutdown()
            server.server_close()
        if streamer:
            streamer.close()
        if output_tmp:
            output_tmp.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stream Rotunda/Juggler screencast frames as HLS or MJPEG."
    )
    parser.add_argument("--endpoint", help="Existing remote Juggler HTTP or WebSocket endpoint.")
    parser.add_argument("--executable-path", help="Rotunda executable to launch when --endpoint is not used.")
    parser.add_argument("--url", help="Optional URL to open before streaming.")
    parser.add_argument("--headless", action="store_true", help="Launch headless when not attaching to an endpoint.")
    parser.add_argument("--debug", action="store_true", help="Enable Rotunda launch debug logging.")
    parser.add_argument("--new-context", action="store_true", help="Create a new context when attaching to an endpoint.")
    parser.add_argument("--new-page", action="store_true", help="Create a new page when attaching to an endpoint.")
    parser.add_argument("--page-index", type=int, default=0, help="Existing page index to stream when attaching.")
    parser.add_argument("--viewport", type=parse_viewport, default={"width": 1280, "height": 720})
    parser.add_argument("--capture-size", type=parse_viewport, help="Juggler JPEG frame size. Defaults to the page viewport.")
    parser.add_argument("--quality", type=int, default=95, help="Juggler JPEG quality, 1-100.")
    parser.add_argument("--fps", type=int, default=25, help="Output stream FPS.")
    parser.add_argument("--seed-screenshot", action=argparse.BooleanOptionalAction, default=False, help="Seed the stream with a JPEG screenshot before live frames arrive. Off by default so the stream size is set by the first Juggler frame.")
    parser.add_argument("--print-frames", type=int, default=0, help="Print every N received Juggler frames.")
    parser.add_argument("--mode", choices=["hls", "mjpeg"], default="hls")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--output-dir", type=Path, help="HLS output directory. Defaults to a temp directory.")
    parser.add_argument("--ffmpeg", help="Path to ffmpeg. Defaults to PATH lookup.")
    parser.add_argument("--codec", default="libx264", help="ffmpeg video codec for HLS mode.")
    parser.add_argument("--crf", type=int, default=18, help="H.264 CRF quality for HLS mode. Lower is better; 18 is visually high quality.")
    parser.add_argument("--hls-time", type=float, default=0.6, help="HLS segment length in seconds. Lower reduces latency.")
    parser.add_argument("--hls-list-size", type=int, default=2, help="Number of HLS segments advertised in the live playlist.")
    parser.add_argument("--ffmpeg-loglevel", default="warning")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not 1 <= args.fps <= 60:
        raise SystemExit("--fps must be between 1 and 60")
    if not 1 <= args.quality <= 100:
        raise SystemExit("--quality must be between 1 and 100")
    if not 0 <= args.crf <= 51:
        raise SystemExit("--crf must be between 0 and 51")
    if args.hls_time < 0.6:
        raise SystemExit("--hls-time must be at least 0.6 for broadly compatible HLS playlists")
    if args.hls_list_size < 1:
        raise SystemExit("--hls-list-size must be at least 1")
    asyncio.run(stream(args))


if __name__ == "__main__":
    main()
