# Native viewport and selector video

Viewport and selector streams share one encoder pipeline:

`WebRender compositor readback / element paint recording → fixed native video surface → Gecko PEMFactory → Annex B`

Juggler transports compressed packets with their native timestamps. Two
clients consume them; PNG, Python image work, FFmpeg, and MSE are absent:

- `scripts/stream-selector-webrtc.py` — the primary client. Repacketizes the
  Annex B stream as RTP/SRTP and serves a WebRTC viewer page, for embedding a
  live browser preview in another web application. The RSE2 crop rectangle
  rides a `metadata` data channel so the viewer presents only the selected
  element's region of the fixed decoder canvas.
- `scripts/stream-selector-low-latency.py` — chunked-HTTP/WebCodecs client;
  the integration-test harness and local benchmark.

| Platform | Working path | 4K60 target |
| --- | --- | --- |
| macOS | Core Animation layer tree / element surface → NV12 `IOSurface` → VideoToolbox HEVC | Headed compositor IOSurface path; throughput scales with output size (measurements below). Headless uses a CPU WebRender readback that preserves correctness but is not the 4K60 design |
| Microsoft Windows | Not currently supported | D3D11 texture wrapped with `MFCreateDXGISurfaceBuffer` |
| Linux | Not currently supported | DMA-BUF imported by VAAPI/NVENC in RDD/GPU |

Native video requests are rejected outside macOS by both the Playwright API and
Rotunda's Juggler backend. We welcome contributions for Linux and Microsoft
Windows; production support needs GPU-native frame transport and codec
negotiation rather than relying on the current CPU fallback.

Frames use a fixed even-sized canvas so resizes do not recreate the encoder.
Selector recordings preserve their native size, composited over opaque white
inside the content rectangle; the letterbox outside it is black.
Selector-less recordings read the already-composited WebRender window, crop out
browser controls, and fill the output canvas. This avoids OS screen-capture
permissions, occlusion, and off-screen-window failures. Snapshot readback is
expanded in place to Gecko's aligned `BufferTexture` stride before the exact
browser crop is copied into the encoder path.

On Apple Silicon the macOS path uses eight bounded frames in flight, per-frame
VideoToolbox completions, synchronized Core Image RGB→NV12 compositing, and HEVC
for 4K. Headed capture renders the already-composited Core Animation layer tree
directly into an IOSurface on the compositor thread, avoiding the synchronous
CPU WebRender readback. Sustained throughput is bounded by the synchronous
main-thread Core Image composite into the fixed output canvas, so it scales
with output size and machine load: 15-second headed measurements of the full
1280×720 browser viewport reached ~30 fps at 3840×2160 and ~45 fps at
1920×1080 on a loaded development machine (an earlier idle-machine run
sustained 59.52 fps at 4K), always with zero decoder errors in real Chrome.
Chaining the Core Image completion into the encoder instead of blocking the
main thread is the known lever for lifting this bound.

The binary callback may contain multiple frames. `RSE2` carries the centered
content rectangle for that exact encoded frame, letting clients keep a fixed
decoder while a canvas or popover shrink-wraps the resizing element. Each is:

`RSE2 | flags:u8 | size:u32be | pts_us:u64be | duration_us:u32be | width:u32be | height:u32be | crop_x:u32be | crop_y:u32be | crop_width:u32be | crop_height:u32be | Annex-B bytes`

A capture error (for example the selected element being detached) stops the
screencast rather than erroring silently; restart the stream after re-resolving
the selector.

Run the real browser integration and local benchmark with:

```sh
uv run --project . --package rotunda-tests --group playwright-tests \
  pytest -q --integration --headless \
  __tests__/playwright/async/test_element_stream_video.py

python3 -m http.server 8765 &  # serves scripts/resizing-element-stream-demo.html
uv run scripts/stream-selector-low-latency.py \
  --url http://127.0.0.1:8765/scripts/resizing-element-stream-demo.html \
  --video-size 3840x2160 --fps 60 --bitrate-mbps 35 \
  --benchmark-seconds 15 --verify-client
```

Add `--selector '#stream-target'` to stream only that DOM subtree instead.
`scripts/stream-selector-webrtc.py` takes the same driving options and serves
the WebRTC viewer instead of the chunked-HTTP one.
