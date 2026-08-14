#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "click>=8.1",
#   "rotunda",
# ]
#
# [tool.uv.sources]
# rotunda = { path = "../pythonlib", editable = true }
# ///
from __future__ import annotations

import asyncio
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
from types import SimpleNamespace
from typing import Any, ClassVar

import click
from playwright.async_api import async_playwright
from rotunda.screencast import (
    image_size,
    normalize_frame_data,
    parse_viewport,
    resolve_page,
    start_screencast,
)


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
                content_type = (
                    b"image/png" if frame.startswith(b"\x89PNG\r\n\x1a\n") else b"image/jpeg"
                )
                self.wfile.write(b"Content-Type: " + content_type + b"\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return


class HlsHandler(SimpleHTTPRequestHandler):
    extensions_map: ClassVar[dict[str, str]] = {
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


def format_size(size: dict[str, int]) -> str:
    return f"{size['width']}x{size['height']}"


def validate_capture_size(size: dict[str, int], args: SimpleNamespace) -> None:
    width = size["width"]
    height = size["height"]
    if width < 10 or width > 10000 or height < 10 or height > 10000:
        raise SystemExit(f"Invalid capture size {format_size(size)}.")
    if args.mode == "hls" and (width % 2 or height % 2):
        raise SystemExit(
            "HLS output is encoded as yuv420p H.264 and needs even dimensions. "
            f"Use an even --viewport or --capture-size instead of {format_size(size)}."
        )


async def resolve_capture_size(page: Any, args: SimpleNamespace) -> dict[str, int]:
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


async def stream(args: SimpleNamespace) -> None:
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
                if args.selector:
                    await page.locator(args.selector).first.wait_for(
                        state="visible", timeout=15_000
                    )
                    print(f"Element selector: {args.selector}", flush=True)
                    capture_size = None
                else:
                    capture_size = await resolve_capture_size(page, args)
                    print(
                        f"Requested capture size: {format_size(capture_size)}",
                        flush=True,
                    )
                frame_count = 0
                actual_frame_size: dict[str, int] | None = None

                def on_frame(frame: dict[str, Any]) -> None:
                    nonlocal actual_frame_size, frame_count
                    frame_data = normalize_frame_data(frame["data"])
                    new_size = image_size(frame_data)
                    if new_size and new_size != actual_frame_size:
                        actual_frame_size = new_size
                        print(
                            f"Actual frame size: {format_size(actual_frame_size)}",
                            flush=True,
                        )
                    frame_source.update(frame_data)
                    frame_count += 1
                    if args.print_frames and frame_count % args.print_frames == 0:
                        print(f"frames={frame_count}", flush=True)

                await start_screencast(
                    page,
                    on_frame,
                    args.quality,
                    capture_size,
                    selector=args.selector,
                    fps=args.fps,
                )
                try:
                    if args.seed_screenshot and capture_size:
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


def _viewport_callback(
    _ctx: click.Context,
    _param: click.Parameter,
    value: str | None,
) -> dict[str, int] | None:
    if value is None:
        return None
    try:
        return parse_viewport(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc


def _validate_range(param_hint: str, value: int | float, lower: int | float, upper: int | float) -> None:
    if not lower <= value <= upper:
        raise click.BadParameter(f"must be between {lower} and {upper}", param_hint=param_hint)


@click.command(
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Stream Rotunda/Juggler screencast frames as HLS or multipart images.",
)
@click.option("--endpoint", help="Existing remote Juggler HTTP or WebSocket endpoint.")
@click.option("--executable-path", help="Rotunda executable to launch when --endpoint is not used.")
@click.option("--url", help="Optional URL to open before streaming.")
@click.option("--headless", is_flag=True, help="Launch headless when not attaching to an endpoint.")
@click.option("--debug", is_flag=True, help="Enable Rotunda launch debug logging.")
@click.option("--new-context", is_flag=True, help="Create a new context when attaching to an endpoint.")
@click.option("--new-page", is_flag=True, help="Create a new page when attaching to an endpoint.")
@click.option("--page-index", type=int, default=0, show_default=True, help="Existing page index to stream when attaching.")
@click.option("--selector", help="Stream the first matching element as isolated transparent PNG frames.")
@click.option("--viewport", default="1280x720", show_default=True, callback=_viewport_callback, help="Browser viewport, formatted as WIDTHxHEIGHT.")
@click.option("--capture-size", callback=_viewport_callback, help="Juggler JPEG frame size. Defaults to the page viewport.")
@click.option("--quality", type=int, default=95, show_default=True, help="Juggler page-stream JPEG quality, 1-100. Selector PNG streams ignore it.")
@click.option("--fps", type=int, default=25, show_default=True, help="Output stream FPS.")
@click.option("--seed-screenshot/--no-seed-screenshot", default=False, show_default=True, help="Seed the stream with a JPEG screenshot before live frames arrive. Off by default so the stream size is set by the first Juggler frame.")
@click.option("--print-frames", type=int, default=0, show_default=True, help="Print every N received Juggler frames.")
@click.option("--mode", type=click.Choice(["hls", "mjpeg"]), help="Output format. Defaults to MJPEG with --selector, otherwise HLS.")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=int, default=8899, show_default=True)
@click.option("--output-dir", type=click.Path(file_okay=False, dir_okay=True, path_type=Path), help="HLS output directory. Defaults to a temp directory.")
@click.option("--ffmpeg", help="Path to ffmpeg. Defaults to PATH lookup.")
@click.option("--codec", default="libx264", show_default=True, help="ffmpeg video codec for HLS mode.")
@click.option("--crf", type=int, default=18, show_default=True, help="H.264 CRF quality for HLS mode. Lower is better; 18 is visually high quality.")
@click.option("--hls-time", type=float, default=0.6, show_default=True, help="HLS segment length in seconds. Lower reduces latency.")
@click.option("--hls-list-size", type=int, default=2, show_default=True, help="Number of HLS segments advertised in the live playlist.")
@click.option("--ffmpeg-loglevel", default="warning", show_default=True)
def main(**kwargs: Any) -> None:
    kwargs["mode"] = kwargs["mode"] or ("mjpeg" if kwargs["selector"] else "hls")
    if kwargs["selector"] and kwargs["mode"] != "mjpeg":
        raise click.BadParameter(
            "element streams require MJPEG so frame dimensions can follow the element",
            param_hint="--mode",
        )
    _validate_range("--fps", kwargs["fps"], 1, 60)
    _validate_range("--quality", kwargs["quality"], 1, 100)
    _validate_range("--crf", kwargs["crf"], 0, 51)
    if kwargs["hls_time"] < 0.6:
        raise click.BadParameter(
            "must be at least 0.6 for broadly compatible HLS playlists",
            param_hint="--hls-time",
        )
    if kwargs["hls_list_size"] < 1:
        raise click.BadParameter("must be at least 1", param_hint="--hls-list-size")
    asyncio.run(stream(SimpleNamespace(**kwargs)))


if __name__ == "__main__":
    main()
