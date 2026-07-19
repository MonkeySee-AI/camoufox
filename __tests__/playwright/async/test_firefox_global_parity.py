# Copyright (c) 2026 Pierce Freeman.

import asyncio
import json
import os
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.async_api import Playwright
from rotunda import AsyncNewContext, async_connect_over_remote_juggler

from .test_remote_juggler import _launch_remote_juggler, _terminate_process

_PROBE_HTML = rb"""<!doctype html><meta charset="utf-8"><script>
(() => {
  const describe = object => Reflect.ownKeys(object).map(key => {
    const descriptor = Object.getOwnPropertyDescriptor(object, key);
    const value = descriptor && "value" in descriptor ? descriptor.value : undefined;
    return {
      key: typeof key === "symbol" ? `Symbol(${key.description || ""})` : key,
      configurable: descriptor ? descriptor.configurable : null,
      enumerable: descriptor ? descriptor.enumerable : null,
      kind: descriptor && "value" in descriptor ? "data" : "accessor",
      valueType: descriptor && "value" in descriptor ? typeof value : null,
      functionName: typeof value === "function" ? value.name : null,
      functionLength: typeof value === "function" ? value.length : null,
      getterType: descriptor && !("value" in descriptor) ? typeof descriptor.get : null,
      setterType: descriptor && !("value" in descriptor) ? typeof descriptor.set : null,
    };
  });

  Promise.resolve().then(() => setTimeout(async () => {
    const prototypeChain = [];
    for (let current = window; current; current = Object.getPrototypeOf(current)) {
      prototypeChain.push({
        tag: Object.prototype.toString.call(current),
        properties: describe(current),
      });
    }
    const stack = new Error("firefox-global-parity").stack || "";
    const asyncParentFrames = stack.split("\n")
      .filter(line => /^(?:promise callback|setTimeout handler)\*/.test(line))
      .map(line => line.split("@", 1)[0]);
    const browser = new URL(location.href).searchParams.get("browser");
    await fetch(`/report?browser=${encodeURIComponent(browser)}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({prototypeChain, asyncParentFrames}),
    });
  }, 0));
})();
</script>"""


@contextmanager
def _probe_server() -> Iterator[
    tuple[str, dict[str, dict[str, Any]], dict[str, threading.Event]]
]:
    reports: dict[str, dict[str, Any]] = {}
    events = {name: threading.Event() for name in ("stock", "rotunda")}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            if urlparse(self.path).path != "/probe":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_PROBE_HTML)))
            self.end_headers()
            self.wfile.write(_PROBE_HTML)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            browser = parse_qs(parsed.query).get("browser", [""])[0]
            if parsed.path != "/report" or browser not in events:
                self.send_error(400)
                return
            length = int(self.headers.get("Content-Length", "0"))
            reports[browser] = json.loads(self.rfile.read(length))
            events[browser].set()
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", reports, events
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


async def _wait_for_report(event: threading.Event, browser: str) -> None:
    assert await asyncio.to_thread(event.wait, 30), (
        f"Timed out waiting for {browser} report"
    )


async def _capture_stock_firefox(
    executable: str,
    profile_dir: Path,
    probe_url: str,
    event: threading.Event,
) -> None:
    profile_dir.mkdir()
    process = await asyncio.create_subprocess_exec(
        executable,
        "--headless",
        "-no-remote",
        "-profile",
        str(profile_dir),
        f"{probe_url}/probe?browser=stock",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await _wait_for_report(event, "stock Firefox")
    finally:
        await _terminate_process(process)


async def _capture_rotunda(
    playwright: Playwright,
    executable: str,
    profile_dir: Path,
    probe_url: str,
    event: threading.Event,
) -> None:
    profile_dir.mkdir()
    browser = None
    process, ws_endpoint, _logs, readers = await _launch_remote_juggler(
        executable,
        profile_dir,
    )
    try:
        browser = await async_connect_over_remote_juggler(playwright, ws_endpoint)
        context = await AsyncNewContext(browser)
        page = await context.new_page()
        await page.goto(
            f"{probe_url}/probe?browser=rotunda", wait_until="domcontentloaded"
        )
        await _wait_for_report(event, "Rotunda")
        await context.close()
        await browser.close()
        browser = None
    finally:
        if browser is not None and browser.is_connected():
            await browser.close()
        await _terminate_process(process)
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


def _surface_diff(stock: dict[str, Any], rotunda: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    stock_chain = stock["prototypeChain"]
    rotunda_chain = rotunda["prototypeChain"]
    if len(stock_chain) != len(rotunda_chain):
        diff["prototypeChainLength"] = [len(stock_chain), len(rotunda_chain)]
    for index, (stock_level, rotunda_level) in enumerate(
        zip(stock_chain, rotunda_chain)
    ):
        # Firefox lazily resolves a few native globals in startup-path-dependent
        # order. Their presence and descriptors are stable; insertion order is not.
        stock_properties = {item["key"]: item for item in stock_level["properties"]}
        rotunda_properties = {item["key"]: item for item in rotunda_level["properties"]}
        level_diff: dict[str, Any] = {}
        if stock_level["tag"] != rotunda_level["tag"]:
            level_diff["tag"] = [stock_level["tag"], rotunda_level["tag"]]
        if missing := sorted(stock_properties.keys() - rotunda_properties.keys()):
            level_diff["missingFromRotunda"] = missing
        if added := sorted(rotunda_properties.keys() - stock_properties.keys()):
            level_diff["addedByRotunda"] = added
        changed = {
            key: [stock_properties[key], rotunda_properties[key]]
            for key in stock_properties.keys() & rotunda_properties.keys()
            if stock_properties[key] != rotunda_properties[key]
        }
        if changed:
            level_diff["changedDescriptors"] = changed
        if level_diff:
            diff[f"prototypeChain[{index}]"] = level_diff
    if stock["asyncParentFrames"] != rotunda["asyncParentFrames"]:
        diff["asyncParentFrames"] = [
            stock["asyncParentFrames"],
            rotunda["asyncParentFrames"],
        ]
    return diff


async def test_rotunda_page_globals_match_stock_firefox(
    playwright: Playwright,
    tmp_path: Path,
) -> None:
    rotunda = os.getenv("ROTUNDA_EXECUTABLE_PATH")
    stock = os.getenv("STOCK_FIREFOX_EXECUTABLE_PATH")
    if not stock and sys.platform == "darwin":
        stock = "/Applications/Firefox.app/Contents/MacOS/firefox"
    if not rotunda or not Path(rotunda).is_file():
        pytest.skip("Global parity requires ROTUNDA_EXECUTABLE_PATH.")
    if not stock or not Path(stock).is_file():
        pytest.skip("Global parity requires STOCK_FIREFOX_EXECUTABLE_PATH.")

    # The page reports its own observable surface, so stock Firefox needs no
    # automation transport and Rotunda is measured after its real profile init.
    with _probe_server() as (probe_url, reports, events):
        await _capture_stock_firefox(
            stock,
            tmp_path / "stock-profile",
            probe_url,
            events["stock"],
        )
        await _capture_rotunda(
            playwright,
            rotunda,
            tmp_path / "rotunda-profile",
            probe_url,
            events["rotunda"],
        )

    diff = _surface_diff(reports["stock"], reports["rotunda"])
    assert not diff, "Page-visible Firefox global mismatch:\n" + json.dumps(
        diff,
        indent=2,
        sort_keys=True,
    )
