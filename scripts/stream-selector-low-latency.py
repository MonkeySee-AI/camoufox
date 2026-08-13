#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "rotunda",
# ]
#
# [tool.uv.sources]
# rotunda = { path = "../pythonlib", editable = true }
# ///
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO

from playwright.async_api import async_playwright

ROOT = Path(__file__).parents[1]
CORE_SPEC = importlib.util.spec_from_file_location(
    "stream_juggler_screencast", ROOT / "scripts" / "stream-juggler-screencast.py"
)
assert CORE_SPEC and CORE_SPEC.loader
CORE = importlib.util.module_from_spec(CORE_SPEC)
CORE_SPEC.loader.exec_module(CORE)


def read_mp4_box(stream: BinaryIO) -> tuple[bytes, bytes] | None:
    header = stream.read(8)
    if not header:
        return None
    if len(header) != 8:
        raise EOFError("truncated MP4 box header")
    size = int.from_bytes(header[:4], "big")
    box_type = header[4:8]
    if size == 1:
        extended = stream.read(8)
        if len(extended) != 8:
            raise EOFError("truncated extended MP4 box header")
        header += extended
        size = int.from_bytes(extended, "big")
    if size < len(header):
        raise ValueError(f"invalid {box_type!r} MP4 box size {size}")
    body = stream.read(size - len(header))
    if len(body) != size - len(header):
        raise EOFError(f"truncated {box_type!r} MP4 box")
    return box_type, header + body


class FragmentStream:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._init: bytes | None = None
        self._fragments: deque[tuple[int, bytes]] = deque(maxlen=60)
        self._sequence = 0
        self.closed = False

    def set_init(self, data: bytes) -> None:
        with self._condition:
            self._init = data
            self._condition.notify_all()

    def update(self, data: bytes) -> None:
        with self._condition:
            self._sequence += 1
            self._fragments.append((self._sequence, data))
            self._condition.notify_all()

    def wait_for_init(self) -> bytes | None:
        with self._condition:
            while self._init is None and not self.closed:
                self._condition.wait()
            return self._init

    def wait_for_next(self, sequence: int) -> tuple[int, bytes | None]:
        with self._condition:
            while self._sequence <= sequence and not self.closed:
                self._condition.wait()
            for fragment_sequence, fragment in self._fragments:
                if fragment_sequence > sequence:
                    return fragment_sequence, fragment
            return self._sequence, None

    def close(self) -> None:
        with self._condition:
            self.closed = True
            self._condition.notify_all()


def h264_level(width: int, height: int) -> tuple[str, str]:
    if width * height > 1920 * 1080:
        return "5.2", "avc1.42C034"
    if width * height > 1280 * 720:
        return "4.2", "avc1.42C02A"
    return "3.2", "avc1.42C020"


def parse_native_frames(packet: bytes) -> list[bytes]:
    frames = []
    offset = 0
    while offset < len(packet):
        if len(packet) - offset < 29 or packet[offset : offset + 4] != b"RSE1":
            raise ValueError("invalid native selector video packet")
        size = int.from_bytes(packet[offset + 5 : offset + 9], "big")
        offset += 29
        if size > len(packet) - offset:
            raise ValueError("truncated native selector video frame")
        frames.append(packet[offset : offset + size])
        offset += size
    return frames


class NativeVideoMuxer:
    def __init__(
        self,
        *,
        ffmpeg: str,
        frame_source: object,
        fragments: FragmentStream,
        fps: int,
        codec: str,
    ) -> None:
        self.codec = codec
        self._process = subprocess.Popen(  # nosec - local developer POC.
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-fflags",
                "+genpts",
                "-use_wallclock_as_timestamps",
                "1",
                "-f",
                "h264",
                "-framerate",
                str(fps),
                "-i",
                "pipe:0",
                "-an",
                "-c:v",
                "copy",
                "-movflags",
                "+empty_moov+default_base_moof+frag_every_frame",
                "-f",
                "mp4",
                "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._frame_source = frame_source
        self._fragments = fragments
        self._stopped = threading.Event()
        self._threads = [
            threading.Thread(target=self._pump_frames, name="selector-video-input", daemon=True),
            threading.Thread(target=self._read_fragments, name="selector-video-output", daemon=True),
            threading.Thread(target=self._drain_stderr, name="selector-video-stderr", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _pump_frames(self) -> None:
        stdin = self._process.stdin
        if stdin is None:
            return
        sequence = 0
        while not self._stopped.is_set() and self._process.poll() is None:
            sequence, packet = self._frame_source.wait_for_next(sequence, timeout=0.1)
            if packet is None:
                if self._frame_source.closed:
                    return
                continue
            try:
                for frame in parse_native_frames(packet):
                    stdin.write(frame)
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return

    def _read_fragments(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            return
        init = bytearray()
        fragment = bytearray()
        try:
            while box := read_mp4_box(stdout):
                box_type, data = box
                if box_type == b"moof":
                    if init:
                        self._fragments.set_init(bytes(init))
                        init.clear()
                    fragment = bytearray(data)
                elif fragment:
                    fragment.extend(data)
                    if box_type == b"mdat":
                        self._fragments.update(bytes(fragment))
                        fragment.clear()
                else:
                    init.extend(data)
        except (EOFError, ValueError):
            pass
        finally:
            self._fragments.close()

    def _drain_stderr(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return
        for line in iter(stderr.readline, b""):
            if line:
                print(f"[ffmpeg] {line.decode(errors='replace').rstrip()}", file=sys.stderr)

    def close(self) -> None:
        self._stopped.set()
        with contextlib.suppress(OSError):
            if self._process.stdin:
                self._process.stdin.close()
        try:
            self._process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            self._process.wait(timeout=3)
        self._fragments.close()


def viewer_html(codec: str) -> bytes:
    return f"""<!doctype html>
<meta charset=utf-8><title>Rotunda low-latency selector stream</title>
<style>
html,body{{margin:0;height:100%;background:#080b12;color:white;font:14px system-ui}}
body{{display:grid;place-items:center;overflow:hidden}}video{{width:100%;height:100%;object-fit:contain}}
#stats{{position:fixed;top:12px;left:12px;padding:7px 10px;border-radius:7px;background:#000a}}
</style>
<video muted autoplay playsinline></video><div id=stats>connecting…</div>
<script>
const video=document.querySelector('video'), stats=document.querySelector('#stats');
const mediaSource=new MediaSource(); video.src=URL.createObjectURL(mediaSource);
window.__decodedFrames=0; window.__presentedFps=0;
let presented=0, measuredFrames=0, measuredAt=performance.now();
const presentedFrame=()=>{{
  presented++;
  const now=performance.now();
  if(now-measuredAt>=1000){{
    window.__presentedFps=(presented-measuredFrames)*1000/(now-measuredAt);
    measuredFrames=presented; measuredAt=now;
  }}
  video.requestVideoFrameCallback(presentedFrame);
}};
video.requestVideoFrameCallback(presentedFrame);
mediaSource.addEventListener('sourceopen',async()=>{{
  const source=mediaSource.addSourceBuffer('video/mp4; codecs="{codec}"');
  const queue=[]; let started=false;
  const pump=()=>{{
    if(source.updating||!queue.length)return;
    source.appendBuffer(queue.shift());
  }};
  source.addEventListener('updateend',()=>{{
    if(video.buffered.length){{
      const end=video.buffered.end(video.buffered.length-1), lag=end-video.currentTime;
      if(!started){{video.currentTime=Math.max(0,end-.03);started=true;}}
      else if(lag>.4)video.currentTime=Math.max(0,end-.05);
      video.playbackRate=lag>.15?1.05:1;
      const trimBefore=video.currentTime-2;
      if(!queue.length&&trimBefore>0&&video.buffered.start(0)<trimBefore){{
        source.remove(0,trimBefore); return;
      }}
    }}
    pump();
  }});
  const reader=(await fetch('/stream.mp4',{{cache:'no-store'}})).body.getReader();
  while(true){{const {{value,done}}=await reader.read();if(done)break;queue.push(value);pump();}}
}});
setInterval(()=>{{
  const quality=video.getVideoPlaybackQuality?.();
  window.__decodedFrames=quality?.totalVideoFrames||window.__decodedFrames;
  const end=video.buffered.length?video.buffered.end(video.buffered.length-1):0;
  stats.textContent=`${{window.__presentedFps.toFixed(1)}} fps · ${{window.__decodedFrames}} decoded · ${{Math.max(0,(end-video.currentTime)*1000).toFixed(0)}} ms buffer · ${{video.videoWidth}}×${{video.videoHeight}}`;
  video.play().catch(()=>{{}});
}},250);
</script>""".encode()


def start_viewer_server(
    host: str, port: int, fragments: FragmentStream, codec: str
) -> ThreadingHTTPServer:
    html = viewer_html(codec)

    class ViewerHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            if self.path in {"/", "/index.html"}:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(html)
                self.close_connection = True
                return
            if self.path != "/stream.mp4":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            init = fragments.wait_for_init()
            if init is None:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            sequence = 0
            try:
                self._write_chunk(init)
                while not fragments.closed:
                    sequence, fragment = fragments.wait_for_next(sequence)
                    if fragment:
                        self._write_chunk(fragment)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _write_chunk(self, data: bytes) -> None:
            self.wfile.write(f"{len(data):X}\r\n".encode())
            self.wfile.write(data)
            self.wfile.write(b"\r\n")
            self.wfile.flush()

    server = ThreadingHTTPServer((host, port), ViewerHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="selector-video-http", daemon=True).start()
    return server


async def stream(args: argparse.Namespace) -> None:
    frame_source = CORE.LatestFrame()
    fragments = FragmentStream()
    _, codec = h264_level(args.video_size["width"], args.video_size["height"])
    video = NativeVideoMuxer(
        ffmpeg=args.ffmpeg,
        frame_source=frame_source,
        fragments=fragments,
        fps=args.fps,
        codec=codec,
    )
    server = start_viewer_server(args.host, args.port, fragments, video.codec)
    host, port = server.server_address[:2]
    print(f"Low-latency viewer: http://{host}:{port}/", flush=True)
    benchmark_frames = 0
    benchmark_bytes = 0
    benchmark_started = benchmark_ended = 0.0

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        async with async_playwright() as playwright:
            browser, page = await CORE.resolve_page(playwright, args)
            try:
                if args.url:
                    await page.goto(args.url)
                await page.locator(args.selector).first.wait_for(state="visible", timeout=15_000)

                def on_frame(frame: dict[str, object]) -> None:
                    nonlocal benchmark_frames, benchmark_bytes
                    packet = CORE.normalize_frame_data(frame["data"])
                    benchmark_frames += len(parse_native_frames(packet))
                    benchmark_bytes += len(packet)
                    frame_source.update(packet)

                benchmark_started = time.monotonic()
                await CORE.start_screencast(
                    page,
                    on_frame,
                    quality=90,
                    size=args.video_size,
                    selector=args.selector,
                    fps=args.fps,
                    video=True,
                    bitrate=round(args.bitrate_mbps * 1_000_000),
                )
                if args.benchmark_seconds:
                    loop.call_later(args.benchmark_seconds, stop.set)
                try:
                    await stop.wait()
                    benchmark_ended = time.monotonic()
                finally:
                    with contextlib.suppress(Exception):
                        await page.screencast.stop()
            finally:
                with contextlib.suppress(Exception):
                    await browser.close()
    finally:
        frame_source.close()
        video.close()
        server.shutdown()
        server.server_close()
    if args.benchmark_seconds:
        elapsed = benchmark_ended - benchmark_started
        print(
            f"Native input: {benchmark_frames} frames, "
            f"{benchmark_frames / elapsed:.2f} fps, "
            f"{benchmark_bytes * 8 / elapsed / 1_000_000:.2f} Mbps",
            flush=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="POC: stream an isolated Rotunda element as low-latency H.264 video."
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--executable-path")
    parser.add_argument("--endpoint")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--new-context", action="store_true")
    parser.add_argument("--new-page", action="store_true")
    parser.add_argument("--page-index", type=int, default=0)
    parser.add_argument("--viewport", type=CORE.parse_viewport, default="1280x720")
    parser.add_argument("--video-size", type=CORE.parse_viewport, default="1280x720")
    parser.add_argument("--fps", type=int, choices=range(1, 61), default=60)
    parser.add_argument("--bitrate-mbps", type=float, default=12)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument(
        "--benchmark-seconds",
        type=float,
        help="stop after this many seconds and report fresh encoded input rate",
    )
    args = parser.parse_args()
    if any(dimension % 2 for dimension in args.video_size.values()):
        parser.error("--video-size dimensions must be even")
    if args.bitrate_mbps <= 0:
        parser.error("--bitrate-mbps must be positive")
    if args.benchmark_seconds is not None and args.benchmark_seconds <= 0:
        parser.error("--benchmark-seconds must be positive")
    return args


if __name__ == "__main__":
    asyncio.run(stream(parse_args()))
