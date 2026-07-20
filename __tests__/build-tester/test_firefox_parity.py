# Copyright (c) 2026 Pierce Freeman.

import asyncio
import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.async_api import Playwright
from __tests__.fixtures.remote_juggler import launch_remote_juggler, terminate_process
from rotunda import AsyncNewContext, async_connect_over_remote_juggler

pytestmark = pytest.mark.integration

_PROBE_HTML = rb"""<!doctype html><meta charset="utf-8"><script>
(() => {
  const functionSource = value => typeof value === "function"
    ? Function.prototype.toString.call(value)
    : null;
  const describe = object => Reflect.ownKeys(object).map(key => {
    const descriptor = Object.getOwnPropertyDescriptor(object, key);
    const value = descriptor && "value" in descriptor ? descriptor.value : undefined;
    return {
      key: typeof key === "symbol" ? `Symbol(${key.description || ""})` : key,
      configurable: descriptor ? descriptor.configurable : null,
      enumerable: descriptor ? descriptor.enumerable : null,
      kind: descriptor && "value" in descriptor ? "data" : "accessor",
      writable: descriptor && "value" in descriptor ? descriptor.writable : null,
      valueType: descriptor && "value" in descriptor ? typeof value : null,
      functionName: typeof value === "function" ? value.name : null,
      functionLength: typeof value === "function" ? value.length : null,
      functionSource: functionSource(value),
      getterType: descriptor && !("value" in descriptor) ? typeof descriptor.get : null,
      setterType: descriptor && !("value" in descriptor) ? typeof descriptor.set : null,
      getterSource: descriptor && !("value" in descriptor)
        ? functionSource(descriptor.get) : null,
      setterSource: descriptor && !("value" in descriptor)
        ? functionSource(descriptor.set) : null,
    };
  });
  const describeValue = value => {
    if (value === null || value === undefined)
      return {type: typeof value, tag: String(value), prototypeChain: []};
    const boxed = Object(value);
    const prototypeChain = [];
    for (let current = boxed; current; current = Object.getPrototypeOf(current)) {
      prototypeChain.push({
        tag: Object.prototype.toString.call(current),
        properties: describe(current),
      });
    }
    return {type: typeof value, tag: Object.prototype.toString.call(value), prototypeChain};
  };
  const normalizeStack = stack => String(stack || "")
    .replace(/browser=(?:stock|rotunda)/g, "browser=firefox");
  const describeError = error => ({
    name: error.name,
    message: error.message,
    string: Error.prototype.toString.call(error),
    stack: normalizeStack(error.stack),
    value: describeValue(error),
  });

  Promise.resolve().then(() => setTimeout(async () => {
    const windowPrototypeChain = [];
    for (let current = window; current; current = Object.getPrototypeOf(current)) {
      windowPrototypeChain.push({
        tag: Object.prototype.toString.call(current),
        properties: describe(current),
      });
    }
    const samples = {
      undefined,
      null: null,
      boolean: false,
      number: 1,
      bigint: 1n,
      string: "rotunda",
      symbol: Symbol("rotunda"),
      object: {},
      array: [1],
      function: function sample(value) { return value; },
      date: new Date(0),
      regexp: /rotunda/gi,
      promise: Promise.resolve(1),
      map: new Map([["key", "value"]]),
      set: new Set(["value"]),
      arrayBuffer: new ArrayBuffer(8),
      uint8Array: new Uint8Array(1),
      url: new URL("https://example.test/path"),
      event: new Event("rotunda"),
      domException: new DOMException("rotunda", "InvalidStateError"),
    };
    const runtimeObjects = Object.fromEntries(Object.entries(samples)
      .map(([name, value]) => [name, describeValue(value)]));
    const synchronousError = (() => {
      function leaf() { return new Error("firefox-global-parity"); }
      return describeError(leaf());
    })();
    const asynchronousError = describeError(new Error("firefox-global-parity-async"));
    let invalidFunctionCall;
    try {
      Function.prototype.toString.call(null);
    } catch (error) {
      invalidFunctionCall = describeError(error);
    }
    const browser = new URL(location.href).searchParams.get("browser");
    await fetch(`/report?browser=${encodeURIComponent(browser)}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        windowPrototypeChain,
        runtimeObjects,
        errors: {synchronousError, asynchronousError, invalidFunctionCall},
      }),
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
        await terminate_process(process)


async def _capture_rotunda(
    playwright: Playwright,
    executable: str,
    profile_dir: Path,
    probe_url: str,
    event: threading.Event,
) -> None:
    profile_dir.mkdir()
    browser = None
    process, ws_endpoint, _logs, readers = await launch_remote_juggler(
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
        await terminate_process(process)
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


def _surface_diff(stock: dict[str, Any], rotunda: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    stock_chain = stock["windowPrototypeChain"]
    rotunda_chain = rotunda["windowPrototypeChain"]
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
            diff[f"windowPrototypeChain[{index}]"] = level_diff
    for surface in ("runtimeObjects", "errors"):
        if stock[surface] != rotunda[surface]:
            diff[surface] = [stock[surface], rotunda[surface]]
    return diff


async def test_rotunda_javascript_surface_matches_stock_firefox(
    playwright: Playwright,
    tmp_path: Path,
    pytestconfig: pytest.Config,
) -> None:
    if not pytestconfig.getoption("--integration"):
        pytest.skip("Firefox parity requires --integration.")
    rotunda = os.getenv("ROTUNDA_EXECUTABLE_PATH")
    stock = os.getenv("STOCK_FIREFOX_EXECUTABLE_PATH")
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
    assert not diff, "Page-visible Firefox JavaScript surface mismatch:\n" + json.dumps(
        diff,
        indent=2,
        sort_keys=True,
    )
