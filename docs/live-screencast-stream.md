# Live Screencast Stream

For a near real-time view of what Rotunda is rendering, use the Juggler screencast streamer. It consumes browser frames directly and serves an HLS stream that QuickTime or VLC can open.

From this repository, launch a stream against the installed Rotunda browser:

```bash
ROTUNDA_EXE="$(uv run python - <<'PY'
from rotunda.pkgman import launch_path
print(launch_path())
PY
)"

uv run scripts/stream-juggler-screencast.py \
  --executable-path "$ROTUNDA_EXE" \
  --url https://24timezones.com/San-Francisco/time \
  --port 8899
```

The script prints a stream URL like:

```text
http://127.0.0.1:8899/stream.m3u8
```

Open it in QuickTime with **File -> Open Location...**, paste the URL, and press **Open**. VLC can open the same URL with **File -> Open Network...**. Chrome does not play raw `.m3u8` HLS playlists directly without a web player extension or page, so use QuickTime or VLC for the bare stream URL.

HLS players keep a small playback buffer, so the viewer may trail the browser by roughly a second or two. If the stream looks more delayed than that after restarting the script, close and reopen the stream URL in QuickTime or VLC so the player drops its old buffer.

## Attach To An Existing Juggler Browser

If you already started Rotunda manually with `--juggler-port 9222`, attach to that browser instead:

```bash
uv run scripts/stream-juggler-screencast.py \
  --endpoint http://127.0.0.1:9222 \
  --url https://24timezones.com/San-Francisco/time \
  --port 8899
```

For more detail on starting Rotunda with a fixed Juggler port, see [Remote Juggler](remote-juggler.md).

Stop the stream with `Ctrl-C`.

## Stream One DOM Element

Pass a Playwright selector to stream only the first matching element:

```bash
uv run scripts/stream-juggler-screencast.py \
  --executable-path "$ROTUNDA_EXE" \
  --url https://24timezones.com/San-Francisco/time \
  --selector "#clock" \
  --port 8899
```

Element streams use the `/mjpeg` multipart endpoint automatically, but each frame is a transparent PNG. Juggler resolves the selector once, then asks Gecko to paint only that element's layout frame and descendants. Page pixels below the element are excluded; shadows and remote iframe descendants remain part of the selected subtree. Resizes therefore produce images at the element's current native ink-overflow dimensions, and ordinary offscreen elements are captured without scrolling the page.

`backdrop-filter` does not import or filter the original page backdrop; Gecko's content-side paint keeps the element's foreground but cannot reproduce that compositor-only effect. A root `mix-blend-mode` blends against transparency, while descendants can still blend with pixels inside the selected subtree. Including the original backdrop for either effect would reintroduce pixels outside the selection.

For the native fixed-size H.264 path and its current platform support, see
[Native selector video](element-video-stream.md).
