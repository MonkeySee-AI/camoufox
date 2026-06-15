# Rotunda Python

Rotunda gives Python agents a Playwright-compatible browser that is built for real web workflows. It launches the Rotunda Firefox build, creates host-compatible browser fingerprints, and gives each context its own identity without forcing you to manage the browser process by hand.

Use it when you want Playwright ergonomics with a browser that is designed for agent-driven browsing instead of vanilla CDP automation.

## Install

```bash
pip install -U rotunda
rotunda fetch
```

Install the GeoIP extra if you use proxies and want Rotunda to align geolocation, timezone, locale, and WebRTC IP with the proxy exit IP:

```bash
pip install -U "rotunda[geoip]"
rotunda fetch
```

If the `rotunda` console command is not on your path, use `python -m rotunda fetch`.

## Quick Start

```python
from rotunda import NewContext, Rotunda

with Rotunda(headless=False) as browser:
    context = NewContext(browser)
    page = context.new_page()

    page.goto("https://example.com")
    print(page.title())
```

`Rotunda(...)` launches the browser. `NewContext(...)` creates a Playwright browser context with a fresh Rotunda fingerprint applied before page scripts run.

Rotunda does not install default add-ons unless you ask for them. If a workflow
needs the bundled default add-ons, launch with `Rotunda(default_addons=True)`.

## Async Usage

```python
import asyncio

from rotunda import AsyncNewContext, AsyncRotunda


async def main():
    async with AsyncRotunda(headless=False) as browser:
        context = await AsyncNewContext(browser)
        page = await context.new_page()

        await page.goto("https://example.com")
        print(await page.title())


asyncio.run(main())
```

## Playwright-Style Launching

If you already use Playwright directly, keep your existing structure and swap in Rotunda's launch helper:

```python
from playwright.sync_api import sync_playwright
from rotunda import NewBrowser, NewContext

with sync_playwright() as playwright:
    browser = NewBrowser(playwright, headless=False)
    context = NewContext(browser)
    page = context.new_page()

    page.goto("https://example.com")
    browser.close()
```

## Proxies And GeoIP

Pass proxies in the same shape Playwright expects. With `rotunda[geoip]`, `geoip=True` derives location data from the current public IP or proxy exit IP.

```python
from rotunda import NewContext, Rotunda

proxy = {
    "server": "http://proxy.example:8080",
    "username": "user",
    "password": "pass",
}

with Rotunda(headless=False, proxy=proxy, geoip=True) as browser:
    context = NewContext(browser, proxy=proxy)
    page = context.new_page()

    page.goto("https://example.com")
```

You can also set context options yourself:

```python
context = NewContext(
    browser,
    locale="en-US",
    timezone_id="America/New_York",
    geolocation={"latitude": 40.7128, "longitude": -74.0060},
)
```

## Headless And Linux Displays

Rotunda defaults to visible browser windows because that best matches agent workflows. On Linux, `headless="virtual"` starts Rotunda in an Xvfb-backed virtual display:

```python
from rotunda import NewContext, Rotunda

with Rotunda(headless="virtual") as browser:
    context = NewContext(browser)
    page = context.new_page()
    page.goto("https://example.com")
```

Install `xvfb` on the host before using virtual display mode.

## Reusing A Fingerprint

Most code should let `NewContext()` generate a fresh identity. If you need the launch fingerprint and context fingerprint to match exactly, generate one fingerprint and pass it to both:

```python
from rotunda import NewContext, Rotunda
from rotunda.fingerprints import generate_fingerprint

fingerprint = generate_fingerprint()

with Rotunda(headless=False, fingerprint=fingerprint) as browser:
    context = NewContext(browser, fingerprint=fingerprint)
    page = context.new_page()
    page.goto("https://example.com")
```

## Debugging A Site

For a quick interactive check, open a Playwright inspector session:

```bash
rotunda test https://example.com
```

For fingerprint reports, enable Rotunda's debug dump around your minimal repro:

```bash
export ROTUNDA_DEBUG_DUMP_DIR=/tmp/rotunda-fingerprint-debug
export ROTUNDA_DEBUG_DUMP=manifest,network,console,vm,returns
export ROTUNDA_VM_ACCESS_SAMPLE_RATE=10

python your_repro_script.py
zip -r rotunda-fingerprint-debug.zip "$ROTUNDA_DEBUG_DUMP_DIR"
```

Review the dump before sharing it. It can include request and response bodies.

## Useful CLI Commands

```bash
rotunda fetch              # install the Rotunda browser used by the Python package
rotunda version            # show package, browser, and GeoIP status
rotunda path               # print the local Rotunda data directory
rotunda test <url>         # open an inspector session for a URL
rotunda remove             # remove local Rotunda browser and data files
```

## Local Development

When developing Rotunda itself, point the Python package at a local browser build:

```bash
source upstream.sh
export ROTUNDA_EXECUTABLE_PATH="$PWD/rotunda-$version-$release/obj-aarch64-apple-darwin/dist/Rotunda.app/Contents/MacOS/rotunda"
uv run --group dev python -m rotunda test --debug
```

On Intel macOS, replace `obj-aarch64-apple-darwin` with `obj-x86_64-apple-darwin`.

All docs live at [rotunda.com/python](https://rotunda.com/python).
