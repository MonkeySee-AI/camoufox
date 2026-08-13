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
import sys
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).parents[1]
CORE_SPEC = importlib.util.spec_from_file_location(
    "stream_juggler_screencast", ROOT / "scripts" / "stream-juggler-screencast.py"
)
assert CORE_SPEC and CORE_SPEC.loader
CORE = importlib.util.module_from_spec(CORE_SPEC)
CORE_SPEC.loader.exec_module(CORE)


class NativePacketStream:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._packets: deque[tuple[int, bytes]] = deque(maxlen=600)
        self._sequence = 0
        self.closed = False

    def update(self, packet: bytes) -> None:
        with self._condition:
            self._sequence += 1
            self._packets.append((self._sequence, packet))
            self._condition.notify_all()

    def sequence(self) -> int:
        with self._condition:
            return self._sequence

    def wait_for_next(self, sequence: int) -> tuple[int, bytes | None]:
        with self._condition:
            while self._sequence <= sequence and not self.closed:
                self._condition.wait()
            for packet_sequence, packet in self._packets:
                if packet_sequence > sequence:
                    return packet_sequence, packet
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


def web_codec(codec: str, width: int, height: int) -> str:
    if codec == "h265":
        return "hvc1.1.6.L156.B0"
    return h264_level(width, height)[1]


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


def viewer_html(codec: str) -> bytes:
    return f"""<!doctype html>
<meta charset=utf-8><title>Rotunda low-latency selector stream</title>
<style>
html,body{{margin:0;height:100%;background:#080b12;color:white;font:14px system-ui}}
body{{display:grid;place-items:center;overflow:hidden}}canvas{{max-width:none;max-height:none}}
#stats{{position:fixed;top:12px;left:12px;padding:7px 10px;border-radius:7px;background:#000a}}
</style>
<canvas></canvas><div id=stats>connecting…</div>
<script>
const canvas=document.querySelector('canvas'), stats=document.querySelector('#stats');
const context=canvas.getContext('2d',{{alpha:false,desynchronized:true}});
window.__decodedFrames=0;window.__presentedFrames=0;window.__presentedFps=0;
window.__decoderError='';window.__streamWidth=0;window.__streamHeight=0;
const frames=[];let playing=false,measuredFrames=0,measuredAt=performance.now();
const present=()=>{{
  const now=performance.now();
  if(now-measuredAt>=1000){{
    window.__presentedFps=(window.__presentedFrames-measuredFrames)*1000/(now-measuredAt);
    measuredFrames=window.__presentedFrames;measuredAt=now;
  }}
  if(frames.length&&(playing||frames.length>=3)){{
    playing=true;
    const frame=frames.shift();
    context.drawImage(frame,0,0,canvas.width,canvas.height);frame.close();
    window.__presentedFrames++;
  }}
  requestAnimationFrame(present);
}};
requestAnimationFrame(present);
(async()=>{{
  const support=await VideoDecoder.isConfigSupported({{
    codec:'{codec}',hardwareAcceleration:'prefer-hardware',optimizeForLatency:true
  }});
  if(!support.supported)throw new Error('WebCodecs does not support {codec}');
  const decoder=new VideoDecoder({{
    output:frame=>{{
      window.__decodedFrames++;
      frames.push(frame);
      while(frames.length>8)frames.shift().close();
    }},
    error:error=>window.__decoderError=String(error),
  }});
  decoder.configure(support.config);
  const reader=(await fetch('/stream.bin',{{cache:'no-store'}})).body.getReader();
  let pending=new Uint8Array(), started=false;
  while(true){{
    const {{value,done}}=await reader.read();if(done)break;
    const joined=new Uint8Array(pending.length+value.length);
    joined.set(pending);joined.set(value,pending.length);pending=joined;
    while(pending.length>=29){{
      if(String.fromCharCode(...pending.subarray(0,4))!=='RSE1')
        throw new Error('invalid native video packet');
      const view=new DataView(pending.buffer,pending.byteOffset);
      const key=!!pending[4], size=view.getUint32(5), packetSize=29+size;
      if(pending.length<packetSize)break;
      const timestamp=Number(view.getBigUint64(9));
      const duration=view.getUint32(17);
      const width=view.getUint32(21),height=view.getUint32(25);
      if(!window.__streamWidth){{
        canvas.width=width;canvas.height=height;
        const rasterScale=Math.min(2,Math.max(1,width/1280,height/720));
        canvas.style.width=`${{width/rasterScale}}px`;
        canvas.style.height=`${{height/rasterScale}}px`;
        window.__streamWidth=width;window.__streamHeight=height;
      }}
      if(started||key){{
        started=true;
        decoder.decode(new EncodedVideoChunk({{
          type:key?'key':'delta',timestamp,duration,
          data:pending.slice(29,packetSize)
        }}));
      }}
      pending=pending.slice(packetSize);
    }}
  }}
}})().catch(error=>window.__decoderError=String(error));
setInterval(()=>{{
  stats.textContent=`${{window.__presentedFps.toFixed(1)}} fps · ${{window.__decodedFrames}} decoded · ${{canvas.width}}×${{canvas.height}}`;
}},250);
</script>""".encode()


def start_viewer_server(
    host: str, port: int, packets: NativePacketStream, codec: str
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
            if self.path != "/stream.bin":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            sequence = packets.sequence()
            try:
                while not packets.closed:
                    sequence, packet = packets.wait_for_next(sequence)
                    if packet:
                        self._write_chunk(packet)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _write_chunk(self, data: bytes) -> None:
            self.wfile.write(f"{len(data):X}\r\n".encode())
            self.wfile.write(data)
            self.wfile.write(b"\r\n")
            self.wfile.flush()

    server = ThreadingHTTPServer((host, port), ViewerHandler)
    server.daemon_threads = True
    threading.Thread(
        target=server.serve_forever, name="selector-video-http", daemon=True
    ).start()
    return server


async def stream(args: argparse.Namespace) -> None:
    packets = NativePacketStream()
    codec_name = args.codec
    if codec_name == "auto":
        codec_name = (
            "h265"
            if sys.platform == "darwin"
            and args.video_size["width"] * args.video_size["height"] > 1920 * 1080
            else "h264"
        )
    codec = web_codec(codec_name, args.video_size["width"], args.video_size["height"])
    server = start_viewer_server(args.host, args.port, packets, codec)
    host, port = server.server_address[:2]
    print(f"Low-latency viewer: http://{host}:{port}/", flush=True)
    benchmark_frames = 0
    benchmark_bytes = 0
    benchmark_started = benchmark_ended = 0.0
    client_start = client_end = None

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        async with async_playwright() as playwright:
            browser, page = await CORE.resolve_page(playwright, args)
            client_browser = viewer = None
            try:
                if args.url:
                    await page.goto(args.url)
                await page.locator(args.selector).first.wait_for(
                    state="visible", timeout=15_000
                )
                if args.verify_client:
                    client_browser = await playwright.chromium.launch(
                        channel="chrome", headless=args.headless
                    )
                    viewer = await client_browser.new_page()
                    await viewer.goto(f"http://{host}:{port}/")

                def on_frame(frame: dict[str, object]) -> None:
                    nonlocal benchmark_frames, benchmark_bytes, benchmark_started
                    packet = CORE.normalize_frame_data(frame["data"])
                    if not benchmark_started:
                        benchmark_started = time.monotonic()
                    benchmark_frames += len(parse_native_frames(packet))
                    benchmark_bytes += len(packet)
                    packets.update(packet)

                await CORE.start_screencast(
                    page,
                    on_frame,
                    quality=90,
                    size=args.video_size,
                    selector=args.selector,
                    fps=args.fps,
                    video=True,
                    bitrate=round(args.bitrate_mbps * 1_000_000),
                    codec=codec_name,
                )
                if viewer:
                    await viewer.wait_for_function(
                        """([width, height]) =>
                          window.__streamWidth === width &&
                          window.__streamHeight === height &&
                          window.__presentedFrames >= 60""",
                        arg=[args.video_size["width"], args.video_size["height"]],
                        timeout=30_000,
                    )
                    benchmark_frames = benchmark_bytes = 0
                    benchmark_started = time.monotonic()
                    client_start = await viewer.evaluate(
                        """() => ({
                          time: performance.now(),
                          decoded: window.__decodedFrames,
                          presented: window.__presentedFrames,
                        })"""
                    )
                if args.benchmark_seconds:
                    loop.call_later(args.benchmark_seconds, stop.set)
                try:
                    await stop.wait()
                    benchmark_ended = time.monotonic()
                    if viewer:
                        client_end = await viewer.evaluate(
                            """() => ({
                              time: performance.now(),
                              decoded: window.__decodedFrames,
                              presented: window.__presentedFrames,
                              width: window.__streamWidth,
                              height: window.__streamHeight,
                              error: window.__decoderError,
                            })"""
                        )
                finally:
                    with contextlib.suppress(Exception):
                        await page.screencast.stop()
            finally:
                if client_browser:
                    with contextlib.suppress(Exception):
                        await client_browser.close()
                with contextlib.suppress(Exception):
                    await browser.close()
    finally:
        packets.close()
        server.shutdown()
        server.server_close()
    if args.benchmark_seconds:
        if not benchmark_started:
            raise RuntimeError("native encoder did not produce a frame")
        elapsed = benchmark_ended - benchmark_started
        print(
            f"Native input: {benchmark_frames} frames, "
            f"{benchmark_frames / elapsed:.2f} fps, "
            f"{benchmark_bytes * 8 / elapsed / 1_000_000:.2f} Mbps",
            flush=True,
        )
        if client_start and client_end:
            client_elapsed = (client_end["time"] - client_start["time"]) / 1000
            decoded = client_end["decoded"] - client_start["decoded"]
            presented = client_end["presented"] - client_start["presented"]
            print(
                f"Chrome client: {int(client_end['width'])}x{int(client_end['height'])}, "
                f"{decoded / client_elapsed:.2f} decoded fps, "
                f"{presented / client_elapsed:.2f} presented fps, "
                f"decoder error: {client_end['error'] or 'none'}",
                flush=True,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="POC: stream an isolated Rotunda element as low-latency native video."
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
    parser.add_argument("--codec", choices=("auto", "h264", "h265"), default="auto")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument(
        "--benchmark-seconds",
        type=float,
        help="stop after this many seconds and report fresh encoded input rate",
    )
    parser.add_argument(
        "--verify-client",
        action="store_true",
        help="open real Chrome and report decoded and presented benchmark rates",
    )
    args = parser.parse_args()
    if any(dimension % 2 for dimension in args.video_size.values()):
        parser.error("--video-size dimensions must be even")
    if args.bitrate_mbps <= 0:
        parser.error("--bitrate-mbps must be positive")
    if args.benchmark_seconds is not None and args.benchmark_seconds <= 0:
        parser.error("--benchmark-seconds must be positive")
    if args.verify_client and args.benchmark_seconds is None:
        parser.error("--verify-client requires --benchmark-seconds")
    return args


if __name__ == "__main__":
    asyncio.run(stream(parse_args()))
