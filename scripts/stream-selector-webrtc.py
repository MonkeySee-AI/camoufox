#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "aiohttp",
#   "aiortc==1.15.0",
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
import hmac
import json
import secrets
import signal
import ssl
import sys
import time
from fractions import Fraction
from typing import Any, NamedTuple

from aiohttp import web
from aiortc import codecs as aiortc_codecs
from aiortc import rtp as aiortc_rtp
from aiortc import rtcrtpsender as aiortc_rtcrtpsender
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCRtpSender,
    RTCSessionDescription,
)
from aiortc.mediastreams import MediaStreamError, convert_timebase
from aiortc.rtcrtpparameters import RTCRtcpFeedback, RTCRtpCodecParameters
from av import Packet
from playwright.async_api import async_playwright
from rotunda.screencast import (
    normalize_frame_data,
    parse_viewport,
    resolve_page,
    start_screencast,
)

RSE2_HEADER_SIZE = 45
VIDEO_TIME_BASE = Fraction(1, 1_000_000)
RTP_VIDEO_TIME_BASE = Fraction(1, 90_000)
RTP_PACKET_MAX = 1_300


class NativeFrame(NamedTuple):
    keyframe: bool
    pts_us: int
    duration_us: int
    crop: tuple[int, int, int, int]
    data: bytes


class H265Packetizer:
    @staticmethod
    def split_bitstream(data: bytes) -> list[bytes]:
        nal_units = []
        offset = 0
        while True:
            start = data.find(b"\x00\x00\x01", offset)
            if start == -1:
                return nal_units
            start += 3
            end = data.find(b"\x00\x00\x01", start)
            if end == -1:
                nal_units.append(data[start:])
                return nal_units
            nal_units.append(
                data[start : end - 1] if data[end - 1] == 0 else data[start:end]
            )
            offset = end

    @staticmethod
    def packetize_nal(nal: bytes) -> list[bytes]:
        if len(nal) < 2:
            raise ValueError("invalid H.265 NAL unit")
        if len(nal) <= RTP_PACKET_MAX:
            return [nal]

        nal_type = (nal[0] >> 1) & 0x3F
        payload_header = bytes([(nal[0] & 0x81) | (49 << 1), nal[1]])
        chunks = [
            nal[offset : offset + RTP_PACKET_MAX - 3]
            for offset in range(2, len(nal), RTP_PACKET_MAX - 3)
        ]
        return [
            payload_header
            + bytes(
                [
                    nal_type
                    | (0x80 if index == 0 else 0)
                    | (0x40 if index == len(chunks) - 1 else 0)
                ]
            )
            + chunk
            for index, chunk in enumerate(chunks)
        ]

    @classmethod
    def packetize(cls, nal_units: list[bytes]) -> list[bytes]:
        payloads = []
        aggregate: list[bytes] = []

        def flush() -> None:
            if len(aggregate) == 1:
                payloads.append(aggregate[0])
            elif aggregate:
                layer_id = min(
                    ((nal[0] & 1) << 5) | ((nal[1] >> 3) & 0x1F) for nal in aggregate
                )
                temporal_id = min(nal[1] & 7 for nal in aggregate)
                payloads.append(
                    bytes([(48 << 1) | (layer_id >> 5), (layer_id << 3) | temporal_id])
                    + b"".join(len(nal).to_bytes(2, "big") + nal for nal in aggregate)
                )
            aggregate.clear()

        for nal in nal_units:
            if len(nal) < 2:
                raise ValueError("invalid H.265 NAL unit")
            if len(nal) > RTP_PACKET_MAX:
                flush()
                payloads.extend(cls.packetize_nal(nal))
            elif (
                2 + sum(2 + len(item) for item in aggregate) + 2 + len(nal)
                > RTP_PACKET_MAX
            ):
                flush()
                aggregate.append(nal)
            else:
                aggregate.append(nal)
        flush()
        return payloads

    def pack(self, packet: Packet) -> tuple[list[bytes], int]:
        payloads = self.packetize(self.split_bitstream(bytes(packet)))
        return payloads, convert_timebase(
            packet.pts, packet.time_base, RTP_VIDEO_TIME_BASE
        )


def install_h265_support() -> None:
    # Chrome uses the RFC 5761 mux-safe 35-63 range for H.265, while aiortc
    # 1.15 only recognizes 96-127 as dynamic during offer/answer remapping.
    aiortc_rtp.DYNAMIC_PAYLOAD_TYPES = (
        *range(35, 64),
        *range(96, 128),
    )
    if not any(
        codec.mimeType.lower() == "video/h265"
        for codec in aiortc_codecs.CODECS["video"]
    ):
        payload_type = (
            max(codec.payloadType or 0 for codec in aiortc_codecs.CODECS["video"]) + 1
        )
        feedback = [
            RTCRtcpFeedback(type="nack"),
            RTCRtcpFeedback(type="nack", parameter="pli"),
            RTCRtcpFeedback(type="goog-remb"),
        ]
        aiortc_codecs.CODECS["video"].extend(
            [
                RTCRtpCodecParameters(
                    mimeType="video/H265",
                    clockRate=90_000,
                    payloadType=payload_type,
                    rtcpFeedback=feedback,
                    parameters={
                        "profile-id": "1",
                        "tier-flag": "0",
                        "level-id": "156",
                        "tx-mode": "SRST",
                    },
                ),
                RTCRtpCodecParameters(
                    mimeType="video/rtx",
                    clockRate=90_000,
                    payloadType=payload_type + 1,
                    parameters={"apt": payload_type},
                ),
            ]
        )

    # aiortc 1.15 has no H.265 packetizer; the sender's private hook is the
    # smallest POC seam and leaves its ICE, DTLS, SRTP, RTCP, and pacing intact.
    original_get_encoder = aiortc_rtcrtpsender.get_encoder
    if not getattr(original_get_encoder, "_rotunda_h265", False):

        def get_encoder(codec: RTCRtpCodecParameters) -> Any:
            if codec.mimeType.lower() == "video/h265":
                return H265Packetizer()
            return original_get_encoder(codec)

        get_encoder._rotunda_h265 = True  # type: ignore[attr-defined]
        aiortc_rtcrtpsender.get_encoder = get_encoder


install_h265_support()


def parse_native_frames(packet: bytes) -> list[NativeFrame]:
    frames = []
    offset = 0
    while offset < len(packet):
        if (
            len(packet) - offset < RSE2_HEADER_SIZE
            or packet[offset : offset + 4] != b"RSE2"
        ):
            raise ValueError("invalid native video packet")
        size = int.from_bytes(packet[offset + 5 : offset + 9], "big")
        pts_us = int.from_bytes(packet[offset + 9 : offset + 17], "big")
        duration_us = int.from_bytes(packet[offset + 17 : offset + 21], "big")
        crop = tuple(
            int.from_bytes(packet[offset + at : offset + at + 4], "big")
            for at in (29, 33, 37, 41)
        )
        payload_start = offset + RSE2_HEADER_SIZE
        payload_end = payload_start + size
        if payload_end > len(packet):
            raise ValueError("truncated native video frame")
        frames.append(
            NativeFrame(
                bool(packet[offset + 4] & 1),
                pts_us,
                duration_us,
                crop,
                packet[payload_start:payload_end],
            )
        )
        offset = payload_end
    return frames


class NativeVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, codec: str, queue_size: int = 8) -> None:
        super().__init__()
        self.codec = codec
        self._queue: asyncio.Queue[Packet | None] = asyncio.Queue(maxsize=queue_size)
        self._awaiting_keyframe = True
        self.keyframe_nals: list[tuple[int, int]] = []
        self.frames = 0
        self.bytes = 0
        self.dropped = 0
        self.crop: tuple[int, int, int, int] | None = None
        self.on_crop: Any = None

    def push(self, packet: bytes) -> None:
        if self.readyState == "ended":
            return
        for frame in parse_native_frames(packet):
            self.frames += 1
            self.bytes += len(frame.data)
            if frame.crop != self.crop:
                self.crop = frame.crop
                if self.on_crop:
                    self.on_crop(frame.crop)
            if frame.keyframe and self.codec == "h265" and not self.keyframe_nals:
                self.keyframe_nals = [
                    ((nal[0] >> 1) & 0x3F, len(nal))
                    for nal in H265Packetizer.split_bitstream(frame.data)
                    if len(nal) >= 2
                ]
            if self._queue.full():
                # Keep one tiny live queue; add adaptive layers if fixed-rate
                # video proves insufficient.
                while not self._queue.empty():
                    self._queue.get_nowait()
                    self.dropped += 1
                self._awaiting_keyframe = True
            if self._awaiting_keyframe and not frame.keyframe:
                self.dropped += 1
                continue
            self._awaiting_keyframe = False
            encoded = Packet(frame.data)
            encoded.pts = encoded.dts = frame.pts_us
            encoded.duration = frame.duration_us
            encoded.time_base = VIDEO_TIME_BASE
            self._queue.put_nowait(encoded)

    async def recv(self) -> Packet:
        packet = await self._queue.get()
        if packet is None:
            raise MediaStreamError
        return packet

    def reset_metrics(self) -> None:
        self.frames = self.bytes = self.dropped = 0

    def stop(self) -> None:
        if self.readyState == "ended":
            return
        super().stop()
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(None)


def viewer_html(
    codec: str, ice_servers: list[dict[str, str]], jitter_buffer_ms: float
) -> bytes:
    return f"""<!doctype html>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Rotunda WebRTC native stream</title>
<style>
html,body{{margin:0;height:100%;background:#080a0f;color:#e8edf7;font:14px system-ui}}
body{{display:grid;grid-template-rows:1fr auto;overflow:hidden}}
#stage{{position:relative;overflow:hidden}}
#frame{{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);overflow:hidden;background:#000}}
video{{position:absolute;left:0;top:0}}
pre{{position:fixed;top:10px;left:10px;margin:0;padding:9px 11px;border-radius:7px;background:#000b;line-height:1.4}}
small{{padding:8px 12px;color:#9ca8bb}}
</style>
<div id=stage><div id=frame><video autoplay muted playsinline></video></div></div><pre>connecting…</pre>
<small>Native Gecko {codec.upper()} → RTP/SRTP pass-through; no decode or re-encode on the server.</small>
<script>
const video=document.querySelector('video'), output=document.querySelector('pre');
const stage=document.getElementById('stage'), frame=document.getElementById('frame');
const pc=new RTCPeerConnection({{iceServers:{json.dumps(ice_servers)}}});
window.__webrtc={{connectionState:'new'}};window.__webrtcSamples=[];
// The RSE2 crop rectangle arrives on the metadata data channel; present only
// that region of the fixed decoder canvas, shrink-wrapped to the viewport.
window.__crop=null;window.__cropHistory=[];
function layout(){{
  const W=video.videoWidth,H=video.videoHeight;
  if(!W||!H)return;
  const crop=window.__crop;
  const region=crop&&crop.width&&crop.height?crop:{{x:0,y:0,width:W,height:H}};
  const scale=Math.min(stage.clientWidth/region.width,stage.clientHeight/region.height);
  frame.style.width=region.width*scale+'px';frame.style.height=region.height*scale+'px';
  video.style.width=W*scale+'px';video.style.height=H*scale+'px';
  video.style.left=-region.x*scale+'px';video.style.top=-region.y*scale+'px';
}}
const metadata=pc.createDataChannel('metadata');
metadata.onmessage=message=>{{
  try{{
    const data=JSON.parse(message.data);
    if(data.type==='crop'&&data.width&&data.height){{window.__crop=data;window.__cropHistory.push(data);layout();}}
  }}catch(error){{}}
}};
new ResizeObserver(layout).observe(stage);
video.addEventListener('resize',layout);
window.__latencySamples=[];window.__receiveToDisplaySamples=[];window.__presentedFrames=0;
let previous=null,resetAt=performance.now(),resetPresented=0,resetCounters={{}};
const transceiver=pc.addTransceiver('video',{{direction:'recvonly'}});
const mimeType='video/{codec}',capabilities=RTCRtpReceiver.getCapabilities('video')?.codecs||[];
const preferred=[...capabilities.filter(c=>c.mimeType.toLowerCase()===mimeType),
  ...capabilities.filter(c=>c.mimeType.toLowerCase()==='video/rtx')];
pc.ontrack=event=>{{
  video.srcObject=new MediaStream([event.track]);video.play().catch(()=>{{}});
  try{{event.receiver.jitterBufferTarget={jitter_buffer_ms};}}catch(error){{}}
}};
pc.onconnectionstatechange=()=>window.__webrtc.connectionState=pc.connectionState;
const waitForIce=()=>new Promise(resolve=>{{
  if(pc.iceGatheringState==='complete')return resolve();
  const changed=()=>{{if(pc.iceGatheringState==='complete'){{pc.removeEventListener('icegatheringstatechange',changed);resolve();}}}};
  pc.addEventListener('icegatheringstatechange',changed);
}});
const average=values=>values.length?values.reduce((a,b)=>a+b,0)/values.length:null;
const percentile=(values,p)=>{{
  if(!values.length)return null;
  const sorted=[...values].sort((a,b)=>a-b);
  return sorted[Math.min(sorted.length-1,Math.floor(sorted.length*p))];
}};
async function sampleStats(){{
  const reports=await pc.getStats();let inbound,codec,pair,local,remote;
  reports.forEach(s=>{{if(s.type==='inbound-rtp'&&s.kind==='video')inbound=s;}});
  if(!inbound)return;
  codec=reports.get(inbound.codecId);
  let transport=reports.get(inbound.transportId);
  pair=transport&&reports.get(transport.selectedCandidatePairId);
  if(!pair)reports.forEach(s=>{{if(s.type==='candidate-pair'&&s.state==='succeeded'&&s.nominated)pair=s;}});
  if(pair){{local=reports.get(pair.localCandidateId);remote=reports.get(pair.remoteCandidateId);}}
  const elapsed=previous?(inbound.timestamp-previous.timestamp):0;
  const countDelta=previous?(inbound.jitterBufferEmittedCount||0)-(previous.jitterBufferEmittedCount||0):0;
  const sample={{
    at:performance.now(),connectionState:pc.connectionState,
    width:video.videoWidth,height:video.videoHeight,
    fps:elapsed>0?((inbound.framesDecoded||0)-(previous.framesDecoded||0))*1000/elapsed:0,
    bitrateMbps:elapsed>0?((inbound.bytesReceived||0)-(previous.bytesReceived||0))*8/elapsed/1000:0,
    packetsLost:inbound.packetsLost||0,framesDecoded:inbound.framesDecoded||0,
    framesDropped:inbound.framesDropped||0,freezeCount:inbound.freezeCount||0,
    jitterMs:(inbound.jitter||0)*1000,
    jitterBufferMs:countDelta>0?((inbound.jitterBufferDelay||0)-(previous.jitterBufferDelay||0))*1000/countDelta:null,
    rttMs:pair?.currentRoundTripTime!=null?pair.currentRoundTripTime*1000:null,
    availableMbps:pair?.availableIncomingBitrate!=null?pair.availableIncomingBitrate/1e6:null,
    codec:codec?.mimeType||'',fmtp:codec?.sdpFmtpLine||'',
    path:`${{local?.candidateType||'?'}}/${{local?.protocol||'?'}} → ${{remote?.candidateType||'?'}}`,
    nackCount:inbound.nackCount||0,pliCount:inbound.pliCount||0,
  }};
  previous=inbound;window.__webrtc=sample;window.__webrtcSamples.push(sample);
  if(window.__webrtcSamples.length>3600)window.__webrtcSamples.shift();
  output.textContent=`${{sample.width}}×${{sample.height}} · ${{sample.fps.toFixed(1)}} fps · ${{sample.bitrateMbps.toFixed(1)}} Mbps\n`+
    `${{sample.connectionState}} · RTT ${{sample.rttMs?.toFixed(1)??'—'}} ms · jitter buffer ${{sample.jitterBufferMs?.toFixed(1)??'—'}} ms\n`+
    `lost ${{sample.packetsLost}} · dropped ${{sample.framesDropped}} · NACK ${{sample.nackCount}} · PLI ${{sample.pliCount}}\n${{sample.codec}} · ${{sample.path}}`;
}}
function presented(now,metadata){{
  window.__presentedFrames++;
  if(Number.isFinite(metadata.captureTime))window.__latencySamples.push(metadata.expectedDisplayTime-metadata.captureTime);
  if(Number.isFinite(metadata.receiveTime))window.__receiveToDisplaySamples.push(metadata.expectedDisplayTime-metadata.receiveTime);
  video.requestVideoFrameCallback(presented);
}}
video.requestVideoFrameCallback(presented);
window.__resetMetrics=()=>{{
  window.__webrtcSamples=[];window.__latencySamples=[];window.__receiveToDisplaySamples=[];
  resetAt=performance.now();resetPresented=window.__presentedFrames;
  resetCounters={{...window.__webrtc}};
}};
window.__summary=()=>{{
  const samples=window.__webrtcSamples.filter(s=>s.fps>0),latest=window.__webrtc;
  const elapsed=(performance.now()-resetAt)/1000;
  return {{...latest,
    fps:elapsed>0?(window.__presentedFrames-resetPresented)/elapsed:0,
    decodedFps:average(samples.map(s=>s.fps)),bitrateMbps:average(samples.map(s=>s.bitrateMbps)),
    packetsLost:(latest.packetsLost||0)-(resetCounters.packetsLost||0),
    framesDropped:(latest.framesDropped||0)-(resetCounters.framesDropped||0),
    freezeCount:(latest.freezeCount||0)-(resetCounters.freezeCount||0),
    latencyP50Ms:percentile(window.__latencySamples,.5),latencyP95Ms:percentile(window.__latencySamples,.95),
    receiveToDisplayP95Ms:percentile(window.__receiveToDisplaySamples,.95),samples:samples.length}};
}};
setInterval(()=>sampleStats().catch(error=>output.textContent=String(error)),1000);
(async()=>{{
  if(!preferred.length)throw new Error(`This browser does not expose ${{mimeType}} for WebRTC`);
  transceiver.setCodecPreferences(preferred);
  await pc.setLocalDescription(await pc.createOffer());await waitForIce();
  const response=await fetch('/offer'+location.search,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(pc.localDescription)}});
  if(!response.ok)throw new Error(await response.text());
  await pc.setRemoteDescription(await response.json());
}})().catch(error=>{{window.__webrtc.error=String(error);output.textContent=String(error);}});
</script>""".encode()


def ice_servers(
    args: argparse.Namespace,
) -> tuple[list[RTCIceServer], list[dict[str, str]]]:
    rtc = []
    browser = []
    for url in args.ice_server:
        kwargs = {"username": args.ice_username, "credential": args.ice_credential}
        rtc.append(RTCIceServer(urls=url, **kwargs))
        browser.append(
            {"urls": url, **{key: value for key, value in kwargs.items() if value}}
        )
    return rtc, browser


async def start_server(
    args: argparse.Namespace, state: dict[str, Any]
) -> tuple[web.AppRunner, str]:
    rtc_ice, browser_ice = ice_servers(args)
    html = viewer_html(args.codec, browser_ice, args.jitter_buffer_ms)
    token = secrets.token_urlsafe(24)

    def authorize(request: web.Request) -> None:
        if not hmac.compare_digest(request.query.get("token", ""), token):
            raise web.HTTPUnauthorized(text="invalid viewer token")

    async def index(request: web.Request) -> web.Response:
        authorize(request)
        return web.Response(body=html, content_type="text/html")

    async def offer(request: web.Request) -> web.Response:
        authorize(request)
        try:
            params = await request.json()
            if params.get("type") != "offer" or not isinstance(params.get("sdp"), str):
                raise ValueError
        except (json.JSONDecodeError, AttributeError, ValueError):
            raise web.HTTPBadRequest(text="expected a WebRTC offer") from None

        async with state["offer_lock"]:
            if old_pc := state.get("pc"):
                await old_pc.close()
            track = NativeVideoTrack(args.codec)
            pc = RTCPeerConnection(RTCConfiguration(iceServers=rtc_ice))
            state.update(pc=pc, track=track)
            # RTP carries only the bitstream, so the RSE2 crop rectangle rides
            # a data channel; the viewer shrink-wraps the presented region.
            # The viewer (offerer) creates the channel — an answer cannot add
            # an m=application section the offer lacked — and it arrives here.
            @pc.on("datachannel")
            def on_datachannel(channel: Any) -> None:
                if channel.label != "metadata":
                    return

                def send_crop(crop: tuple[int, int, int, int]) -> None:
                    if channel.readyState == "open":
                        channel.send(
                            json.dumps(
                                {
                                    "type": "crop",
                                    "x": crop[0],
                                    "y": crop[1],
                                    "width": crop[2],
                                    "height": crop[3],
                                }
                            )
                        )

                track.on_crop = send_crop
                if track.crop:
                    send_crop(track.crop)

            sender = pc.addTrack(track)
            transceiver = next(
                item for item in pc.getTransceivers() if item.sender == sender
            )
            transceiver.setCodecPreferences(
                [
                    codec
                    for codec in RTCRtpSender.getCapabilities("video").codecs
                    if codec.mimeType.lower() == f"video/{args.codec}"
                ]
                + [
                    codec
                    for codec in RTCRtpSender.getCapabilities("video").codecs
                    if codec.mimeType.lower() == "video/rtx"
                ]
            )

            @pc.on("connectionstatechange")
            async def connection_changed() -> None:
                print(f"WebRTC connection: {pc.connectionState}", flush=True)
                if pc.connectionState == "failed":
                    await pc.close()

            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=params["sdp"], type="offer")
            )
            await pc.setLocalDescription(await pc.createAnswer())
            return web.json_response(
                {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
            )

    app = web.Application(client_max_size=1_000_000)
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    context = None
    scheme = "http"
    if args.cert_file:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(args.cert_file, args.key_file)
        scheme = "https"
    site = web.TCPSite(runner, args.host, args.port, ssl_context=context)
    await site.start()
    host, port = runner.addresses[0][:2]
    return runner, f"{scheme}://{host}:{port}/?token={token}"


def format_optional(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.2f}{suffix}"


async def stream(args: argparse.Namespace) -> None:
    state: dict[str, Any] = {"offer_lock": asyncio.Lock(), "pc": None, "track": None}
    runner, viewer_url = await start_server(args, state)
    print(f"WebRTC viewer: {viewer_url}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    client_browser = viewer = None
    summary = None
    started = ended = 0.0
    try:
        async with async_playwright() as playwright:
            browser, page = await resolve_page(playwright, args)
            try:
                await page.goto(args.url)
                if args.selector:
                    await page.locator(args.selector).first.wait_for(
                        state="visible", timeout=15_000
                    )

                def on_frame(frame: dict[str, object]) -> None:
                    track = state.get("track")
                    if track:
                        track.push(normalize_frame_data(frame["data"]))

                await start_screencast(
                    page,
                    on_frame,
                    quality=90,
                    size=args.video_size,
                    selector=args.selector,
                    fps=args.fps,
                    video=True,
                    bitrate=round(args.bitrate_mbps * 1_000_000),
                    codec=args.codec,
                )
                if args.verify_client:
                    client_browser = await playwright.chromium.launch(
                        channel="chrome", headless=args.headless
                    )
                    viewer = await client_browser.new_page()
                    await viewer.goto(viewer_url)
                    try:
                        await viewer.wait_for_function(
                            "([w,h,selector]) => window.__webrtc.framesDecoded >= 30 && document.querySelector('video').videoWidth === w && document.querySelector('video').videoHeight === h && (!selector || window.__crop)",
                            arg=[
                                args.video_size["width"],
                                args.video_size["height"],
                                bool(args.selector),
                            ],
                            timeout=15_000,
                        )
                        crop = await viewer.evaluate("() => window.__crop")
                        if crop:
                            print(
                                f"Viewer crop: {crop['width']}x{crop['height']}"
                                f" at ({crop['x']}, {crop['y']})",
                                flush=True,
                            )
                    except Exception as error:
                        diagnostic = await viewer.evaluate(
                            """() => {
                              const mediaLines = description => description?.sdp.split('\\r\\n').filter(
                                line => /^(m=video|a=(rtpmap|fmtp|sendonly|recvonly))/.test(line));
                              const video = document.querySelector('video');
                              return {stats:window.__webrtc, width:video.videoWidth, height:video.videoHeight,
                                localSdp:mediaLines(pc.localDescription), remoteSdp:mediaLines(pc.remoteDescription),
                                receiver:pc.getReceivers().map(receiver => ({muted:receiver.track.muted,
                                  readyState:receiver.track.readyState, parameters:receiver.getParameters()}))};
                            }"""
                        )
                        track = state["track"]
                        raise RuntimeError(
                            f"WebRTC viewer received no decodable frames; native frames={track.frames}, "
                            f"bytes={track.bytes}, bridge drops={track.dropped}, "
                            f"first keyframe NALs={track.keyframe_nals}, browser={diagnostic}"
                        ) from error
                    track = state["track"]
                    track.reset_metrics()
                    await viewer.evaluate("window.__resetMetrics()")
                    started = time.monotonic()
                if args.benchmark_seconds:
                    loop.call_later(args.benchmark_seconds, stop.set)
                await stop.wait()
                ended = time.monotonic()
                if viewer:
                    summary = await viewer.evaluate("window.__summary()")
            finally:
                with contextlib.suppress(Exception):
                    await page.screencast.stop()
                if client_browser:
                    with contextlib.suppress(Exception):
                        await client_browser.close()
                with contextlib.suppress(Exception):
                    await browser.close()
    finally:
        if pc := state.get("pc"):
            await pc.close()
        await runner.cleanup()

    track = state.get("track")
    if started and track:
        elapsed = ended - started
        print(
            f"Native input: {track.frames / elapsed:.2f} fps, {track.bytes * 8 / elapsed / 1_000_000:.2f} Mbps, {track.dropped} bridge drops",
            flush=True,
        )
    if summary:
        print(
            f"WebRTC client: {summary['width']}x{summary['height']}, {summary['fps']:.2f} presented fps, "
            f"{format_optional(summary['decodedFps'], ' decoded fps')}, {format_optional(summary['bitrateMbps'], ' Mbps')}",
            flush=True,
        )
        print(
            f"Network: {summary['path']}, RTT {format_optional(summary['rttMs'], ' ms')}, "
            f"jitter buffer {format_optional(summary['jitterBufferMs'], ' ms')}, lost {summary['packetsLost']}, "
            f"dropped {summary['framesDropped']}, freezes {summary['freezeCount']}",
            flush=True,
        )
        print(
            f"Latency: p50 {format_optional(summary['latencyP50Ms'], ' ms')}, "
            f"p95 {format_optional(summary['latencyP95Ms'], ' ms')}, "
            f"receive-to-display p95 {format_optional(summary['receiveToDisplayP95Ms'], ' ms')}",
            flush=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="POC: pass Rotunda's native encoded stream through WebRTC."
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--selector")
    parser.add_argument("--executable-path")
    parser.add_argument("--endpoint")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--new-context", action="store_true")
    parser.add_argument("--new-page", action="store_true")
    parser.add_argument("--page-index", type=int, default=0)
    parser.add_argument("--viewport", type=parse_viewport, default="1280x720")
    parser.add_argument("--video-size", type=parse_viewport, default="3840x2160")
    parser.add_argument("--fps", type=int, choices=range(1, 61), default=60)
    parser.add_argument("--bitrate-mbps", type=float, default=35)
    parser.add_argument("--codec", choices=("auto", "h264", "h265"), default="auto")
    parser.add_argument("--jitter-buffer-ms", type=float, default=50)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8901)
    parser.add_argument("--ice-server", action="append", default=[])
    parser.add_argument("--ice-username")
    parser.add_argument("--ice-credential")
    parser.add_argument("--cert-file")
    parser.add_argument("--key-file")
    parser.add_argument("--benchmark-seconds", type=float)
    parser.add_argument("--verify-client", action="store_true")
    args = parser.parse_args()
    if args.codec == "auto":
        args.codec = (
            "h265"
            if sys.platform == "darwin"
            and args.video_size["width"] * args.video_size["height"] > 1920 * 1080
            else "h264"
        )
    if any(dimension % 2 for dimension in args.video_size.values()):
        parser.error("--video-size dimensions must be even")
    if args.bitrate_mbps <= 0:
        parser.error("--bitrate-mbps must be positive")
    if args.jitter_buffer_ms < 0:
        parser.error("--jitter-buffer-ms cannot be negative")
    if bool(args.cert_file) != bool(args.key_file):
        parser.error("--cert-file and --key-file must be supplied together")
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.cert_file:
        parser.error("non-local viewers require --cert-file and --key-file")
    if args.benchmark_seconds is not None and args.benchmark_seconds <= 0:
        parser.error("--benchmark-seconds must be positive")
    if args.verify_client and args.benchmark_seconds is None:
        parser.error("--verify-client requires --benchmark-seconds")
    return args


if __name__ == "__main__":
    asyncio.run(stream(parse_args()))
