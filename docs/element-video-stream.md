# Native viewport and selector video

Viewport and selector streams share one encoder pipeline:

`WebRender compositor readback / element paint recording → fixed native video surface → Gecko PEMFactory → Annex B`

Juggler transports compressed packets with their native timestamps. The POC
client decodes them directly with WebCodecs and presents the newest decoded
frame on a fixed canvas; PNG, Python image work, FFmpeg, and MSE are absent.

| Platform | Working path | 4K60 target |
| --- | --- | --- |
| macOS | Core Animation layer tree / element surface → NV12 `IOSurface` → VideoToolbox HEVC | Implemented and measured at 4K60 headed and headless |
| Microsoft Windows | Not currently supported | D3D11 texture wrapped with `MFCreateDXGISurfaceBuffer` |
| Linux | Not currently supported | DMA-BUF imported by VAAPI/NVENC in RDD/GPU |

Native video requests are rejected outside macOS by both the Playwright API and
Rotunda's Juggler backend. We welcome contributions for Linux and Microsoft
Windows; production support needs GPU-native frame transport and codec
negotiation rather than relying on the current CPU fallback.

Frames use a fixed even-sized canvas so resizes do not recreate the encoder.
Selector recordings preserve their native size and center over opaque white.
Selector-less recordings read the already-composited WebRender window, crop out
browser controls, and fill the output canvas. This avoids OS screen-capture
permissions, occlusion, and off-screen-window failures. Snapshot readback is
expanded in place to Gecko's aligned `BufferTexture` stride before the exact
browser crop is copied into the encoder path.

On Apple Silicon the macOS path uses eight bounded frames in flight, per-frame
VideoToolbox completions, synchronized Core Image RGB→NV12 compositing, and HEVC
for 4K. Headed capture renders the already-composited Core Animation layer tree
directly into an IOSurface on the compositor thread, avoiding the synchronous
CPU WebRender readback. A 15-second headed measurement of the full 1280×720
browser viewport scaled to 3840×2160 sustained 59.52 native and decoded
frames/s and 59.78 presented frames/s in real Chrome with no decoder errors.

The binary callback may contain multiple frames. `RSE2` carries the centered
content rectangle for that exact encoded frame, letting clients keep a fixed
decoder while a canvas or popover shrink-wraps the resizing element. Each is:

`RSE2 | flags:u8 | size:u32be | pts_us:u64be | duration_us:u32be | width:u32be | height:u32be | crop_x:u32be | crop_y:u32be | crop_width:u32be | crop_height:u32be | Annex-B bytes`

Run the real browser integration and local benchmark with:

```sh
uv run --project . --package rotunda-tests --group playwright-tests \
  pytest -q --integration --headless \
  __tests__/playwright/async/test_element_stream_video.py

uv run scripts/stream-selector-low-latency.py \
  --url http://127.0.0.1:8765/scripts/resizing-element-stream-demo.html \
  --video-size 3840x2160 --fps 60 --bitrate-mbps 35 \
  --benchmark-seconds 15 --verify-client
```

Add `--selector '#stream-target'` to stream only that DOM subtree instead.
