# Low-latency element video POC

The image stream is the correctness path: it preserves transparency, follows native ink-overflow bounds, and is easy to inspect frame by frame. It is not the final 4K architecture. Every fresh frame is currently rasterized, PNG-compressed, base64-copied through Juggler, and decoded into a fixed RGB canvas before video encoding.

The low-latency POC replaces the last half of that path with the same shape used by interactive streaming systems:

```text
native isolated element paint
  -> latest-PNG queue (stale source frames are dropped)
  -> cached fixed-size RGB canvas
  -> steady 60 fps raw-frame feed
  -> realtime H.264 VideoToolbox session
  -> one-frame fragmented MP4 over localhost
  -> browser Media Source Extensions + native video decoder
```

Run the demo page server:

```bash
python -m http.server 8765 --bind 127.0.0.1
```

Then start a 60 fps hardware-video stream:

```bash
uv run scripts/stream-selector-low-latency.py \
  --executable-path /path/to/Rotunda \
  --url http://127.0.0.1:8765/scripts/resizing-element-stream-demo.html \
  --selector '#stream-target' \
  --video-size 1280x720 \
  --fps 60
```

Open `http://127.0.0.1:8900/` in Chrome. The overlay reports presented frames per second, decoded frames, buffered latency, and encoded dimensions. Element resizes fit inside the fixed encoder canvas rather than restarting the codec. H.264 has no portable alpha plane, so this POC composites isolated pixels onto `--background` (default `080b12`). The PNG endpoint remains the option when alpha is required.

## Benchmark

On an Apple Silicon development machine, the 1280×720 demo encoded at exactly 60 fps and real headless Chrome decoded 59.8 fps, presented 60 fps, and stayed about 99–136 ms behind the encoder over a five-second sample. The test used VideoToolbox at 12 Mbps over localhost. This measures the video pipeline; fresh visual updates are still capped by selector PNG capture (about 59 fps at 1080p and 16.5 fps at 4K in the same local build).

The encoded cadence can be checked independently of browser scheduling:

```bash
curl --silent --max-time 10 http://127.0.0.1:8900/stream.mp4 -o /tmp/selector.mp4 || true
ffprobe -v error -count_frames -select_streams v:0 \
  -show_entries stream=nb_read_frames,avg_frame_rate /tmp/selector.mp4
```

## What production needs for 4K60

The POC deliberately retains one bridge: Gecko still hands Python a PNG for each selected-element frame. A latest-frame RGB cache isolates the 60 fps encoder from capture jitter, but it cannot invent fresh content. The fixed video stream proves pacing, inter-frame encoding, transport, resizing, and native client decode; it does not make that PNG bridge scale.

The production path should keep the selector display list and Fission dependency resolution added for native element snapshots, then move the remaining work behind one persistent browser-side session:

1. Raster the selected display list into a fixed-size compositor surface at vsync. Keep the producer window unchanged and fit changing ink-overflow bounds inside that surface.
2. Hand the GPU surface directly to a platform pixel buffer (`CVPixelBuffer`/IOSurface on macOS), avoiding PNG, base64, JavaScript, Python, and CPU readback.
3. Keep one hardware encoder alive. Configure VideoToolbox for realtime operation, no B-frames, a short keyframe interval, and a bounded bitrate. Apple exposes realtime and hardware-encoder controls specifically for this use case: [live-streaming VideoToolbox sample](https://developer.apple.com/documentation/videotoolbox/encoding-video-for-live-streaming) and [hardware encoder selection](https://developer.apple.com/documentation/videotoolbox/kvtvideoencoderspecification_enablehardwareacceleratedvideoencoder).
4. Drop stale surfaces before encoding instead of applying request/ack backpressure. Once encoded, preserve bitstream order.
5. Send encoded chunks over WebRTC/RTP for remote use so congestion control and keyframe recovery are built in. The localhost POC uses one-frame fragmented MP4 because TCP loss recovery is effectively free on loopback.
6. Decode into a video surface, not an image element. WebCodecs also exposes hardware preference and realtime latency modes when a raw encoded-chunk client is preferable: [WebCodecs specification](https://w3c.github.io/webcodecs/#hardware-acceleration).
7. Timestamp capture, raster completion, encoder input/output, network arrival, decode, and presentation. Adapt resolution or bitrate before latency queues grow.

That removes every full-frame encode/decode/copy before the actual video codec. The remaining costs—isolated compositor paint, colorspace conversion, hardware encode, localhost transfer, and hardware decode—are the same bounded stages used by cloud-game streaming.
