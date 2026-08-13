# Native selector video

The selector stream has one cross-platform pipeline:

`element paint recording → fixed BGRA video surface → Gecko PEMFactory → H.264 Annex B`

Juggler transports compressed packets only. The client never receives PNG or
uncompressed pixels, and the encoder choice is not part of the protocol.

| Platform | Working path | 4K60 target |
| --- | --- | --- |
| macOS | Direct `IOSurface` / `CVPixelBuffer` into VideoToolbox | Implemented and integration-tested |
| Windows | CPU `SourceSurfaceImage` into Media Foundation | D3D11 texture wrapped with `MFCreateDXGISurfaceBuffer` |
| Linux | CPU `SourceSurfaceImage` into an available Gecko H.264 encoder | DMA-BUF imported by a VAAPI/NVENC encoder in RDD/GPU |

The fallback is real code, not a performance claim: it gives Windows and Linux
one protocol and a correctness path while their native builds and browser
integrations are exercised on those operating systems. Linux must also report
an actionable unsupported-codec error when its Firefox build and system FFmpeg
provide no H.264 encoder. Production should negotiate H.264, VP9, or AV1 rather
than assume every Linux host has H.264 hardware.

Frames use a fixed even-sized canvas so a resizing element does not recreate
the encoder. The element recording is scaled uniformly, centered, and painted
over opaque black.

The OS adapters are not the current throughput limit. A local Apple Silicon
benchmark of the resizing demo measured about 13.3 fresh frames/s for the PNG
path, 13.9 frames/s for native H.264 at 1280x720, and 6.0 frames/s at 3840x2160.
The similar PNG and 720p H.264 rates identify the synchronous per-frame element
display-list recording and cross-process resolution as the bottleneck, not
VideoToolbox or compressed transport.

The next shared change is therefore a persistent offscreen WebRender document
driven at compositor cadence. It should retain the selected element's display
list and Fission dependencies, render the newest state into a small pool of
fixed native surfaces, and let the encoder consume only the newest completed
surface. Juggler request/ACK timing must not clock capture. That one service is
shared; only surface allocation/import and encoder capability selection differ
by operating system.

The binary callback may contain multiple frames. Each is:

`RSE1 | flags:u8 | size:u32be | pts_us:u64be | duration_us:u32be | width:u32be | height:u32be | Annex-B bytes`

Run the native browser integration and local benchmark with:

```sh
uv run --project . --package rotunda-tests --group playwright-tests \
  pytest -q __tests__/playwright/async/test_element_stream_video.py

uv run scripts/stream-selector-low-latency.py \
  --url http://127.0.0.1:8765/scripts/resizing-element-stream-demo.html \
  --selector '#stream-target' --video-size 3840x2160 --fps 60 \
  --benchmark-seconds 10
```
