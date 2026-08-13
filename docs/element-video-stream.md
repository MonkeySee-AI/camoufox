# Native selector video

The selector stream has one shared pipeline:

`element paint recording → fixed native video surface → Gecko PEMFactory → Annex B`

Juggler transports compressed packets with their native timestamps. The POC
client decodes them directly with WebCodecs and presents the newest decoded
frame on a fixed canvas; PNG, Python image work, FFmpeg, and MSE are absent.

| Platform | Working path | 4K60 target |
| --- | --- | --- |
| macOS | Tight native-density BGRA paint → centered NV12 `IOSurface` → VideoToolbox HEVC | Implemented and measured at 4K60 |
| Windows | CPU `SourceSurfaceImage` into Media Foundation H.264 | D3D11 texture wrapped with `MFCreateDXGISurfaceBuffer` |
| Linux | CPU `SourceSurfaceImage` into an available Gecko H.264 encoder | DMA-BUF imported by VAAPI/NVENC in RDD/GPU |

The Windows and Linux fallback is real correctness code, not a 4K60 claim.
Production should negotiate H.264, HEVC, VP9, or AV1 rather than assume every
host and client share one hardware codec.

Frames use a fixed even-sized canvas so a resizing element does not recreate
the encoder. The element recording is scaled uniformly, centered, and painted
over opaque black. It is never enlarged to fill that canvas; macOS rasterizes
at up to Retina density and Core Image composites it without resampling.

On Apple Silicon the macOS path uses eight bounded captures in flight,
per-frame VideoToolbox completions, asynchronous Core Image RGB→NV12 compositing,
and HEVC for 4K. A real Chrome measurement on the resizing demo sustained
58.9 native, decoded, and presented frames/s at 3840×2160 with no decoder
errors.

The binary callback may contain multiple frames. Each is:

`RSE1 | flags:u8 | size:u32be | pts_us:u64be | duration_us:u32be | width:u32be | height:u32be | Annex-B bytes`

Run the real browser integration and local benchmark with:

```sh
uv run --project . --package rotunda-tests --group playwright-tests \
  pytest -q __tests__/playwright/async/test_element_stream_video.py

uv run scripts/stream-selector-low-latency.py \
  --url http://127.0.0.1:8765/scripts/resizing-element-stream-demo.html \
  --selector '#stream-target' --video-size 3840x2160 --fps 60 \
  --benchmark-seconds 15 --verify-client
```
