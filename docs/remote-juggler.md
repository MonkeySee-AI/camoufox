# Remote Juggler

Rotunda can expose Playwright's Firefox Juggler protocol on a local HTTP/WebSocket port. This is useful when one process is responsible for starting the browser and another process connects later to control it.

This is not a CDP endpoint. It exposes Juggler over WebSocket and the Python helper starts a small local bridge that converts that remote Juggler connection into a normal Playwright `Browser`.

## Start Rotunda Manually

Launch Rotunda with a fixed Juggler port:

```bash
/Applications/Rotunda.app/Contents/MacOS/rotunda --juggler-port 9222
```

For a local development build on Apple Silicon:

```bash
source upstream.sh
"$PWD/rotunda-$version-$release/obj-aarch64-apple-darwin/dist/Rotunda.app/Contents/MacOS/rotunda" --juggler-port 9222
```

For Intel macOS, replace `obj-aarch64-apple-darwin` with `obj-x86_64-apple-darwin`.

Use a fixed port when another process needs to connect later. `--juggler-port 0` is supported, but the launcher must read Rotunda's startup output to discover the chosen port.

You can verify that the endpoint is ready with:

```bash
curl http://127.0.0.1:9222/json/version
```

The response includes `webSocketDebuggerUrl`, for example:

```json
{
  "Browser": "Rotunda/Juggler",
  "Protocol-Version": "1.0",
  "User-Agent": "Rotunda",
  "webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/<id>"
}
```

## Connect From Python

Use `connect_over_remote_juggler` with Playwright's normal sync API:

```python
from playwright.sync_api import sync_playwright
from rotunda import connect_over_remote_juggler


with sync_playwright() as playwright:
    browser = connect_over_remote_juggler(
        playwright,
        "http://127.0.0.1:9222",
    )

    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())

    browser.close()
```

The helper accepts either the HTTP origin or the raw WebSocket URL:

```python
browser = connect_over_remote_juggler(
    playwright,
    "ws://127.0.0.1:9222/devtools/browser/<id>",
)
```

## Async API

```python
import asyncio

from playwright.async_api import async_playwright
from rotunda import async_connect_over_remote_juggler


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await async_connect_over_remote_juggler(
            playwright,
            "http://127.0.0.1:9222",
        )

        page = await browser.new_page()
        await page.goto("https://example.com")
        print(await page.title())

        await browser.close()


asyncio.run(main())
```

## Notes

- Do not call `playwright.firefox.connect()` directly with the `--juggler-port` WebSocket URL. That endpoint speaks raw Juggler, not Playwright's remote protocol.
- `connect_over_remote_juggler` starts a local Node bridge process and closes it when the returned browser is closed.
- The browser currently binds the Juggler HTTP/WebSocket server to localhost. For a different machine, use SSH port forwarding or add a host-binding option.
- Keep the Python package's Playwright version paired with the Rotunda build. The bridge uses Playwright's Firefox/Juggler internals, so arbitrary Playwright upgrades should be validated before release.
