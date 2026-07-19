# Copyright (c) 2026 Pierce Freeman.

import asyncio
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import pytest
from playwright.async_api import Playwright, expect
from rotunda import async_connect_over_remote_juggler

_JUGGLER_ENDPOINT_RE = re.compile(r"Juggler listening on (ws://\S+)")


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=5)


async def _collect_output(
    stream: asyncio.StreamReader | None,
    label: str,
    endpoint_future: asyncio.Future[str],
    logs: list[str],
) -> None:
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode(errors="replace").rstrip()
        logs.append(f"{label}: {text}")
        match = _JUGGLER_ENDPOINT_RE.search(text)
        if match and not endpoint_future.done():
            endpoint_future.set_result(match.group(1))


async def _launch_remote_juggler(
    executable_path: str,
    profile_dir: Path,
) -> tuple[asyncio.subprocess.Process, str, list[str], list[asyncio.Task[None]]]:
    env = os.environ.copy()
    env.pop("ROTUNDA_CONFIG_PATH", None)

    process = await asyncio.create_subprocess_exec(
        executable_path,
        "--headless",
        "--profile",
        str(profile_dir),
        "--juggler-port",
        "0",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    logs: list[str] = []
    endpoint_future = asyncio.get_running_loop().create_future()
    readers = [
        asyncio.create_task(_collect_output(process.stdout, "stdout", endpoint_future, logs)),
        asyncio.create_task(_collect_output(process.stderr, "stderr", endpoint_future, logs)),
    ]
    process_wait = asyncio.create_task(process.wait())

    try:
        done, _ = await asyncio.wait(
            {endpoint_future, process_wait},
            timeout=30,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if endpoint_future in done:
            return process, endpoint_future.result(), logs, readers
        if process_wait in done:
            raise AssertionError(
                "Rotunda exited before reporting a Juggler endpoint.\n"
                + "\n".join(logs[-50:])
            )
        raise AssertionError(
            "Timed out waiting for Rotunda to report a Juggler endpoint.\n"
            + "\n".join(logs[-50:])
        )
    except Exception:
        await _terminate_process(process)
        raise
    finally:
        process_wait.cancel()


def _version_url(ws_endpoint: str) -> str:
    parsed = urlparse(ws_endpoint)
    return f"http://{parsed.netloc}/json/version"


def _read_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


async def test_should_connect_over_remote_juggler_port(
    playwright: Playwright,
    tmp_path: Path,
) -> None:
    executable_path = os.getenv("ROTUNDA_EXECUTABLE_PATH")
    if not executable_path:
        pytest.skip("Remote Juggler integration requires ROTUNDA_EXECUTABLE_PATH.")

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    browser = None
    process, ws_endpoint, _logs, readers = await _launch_remote_juggler(
        executable_path,
        profile_dir,
    )

    try:
        version = await asyncio.to_thread(_read_json, _version_url(ws_endpoint))
        assert version["Browser"] == "Rotunda/Juggler"
        assert version["webSocketDebuggerUrl"] == ws_endpoint

        browser = await async_connect_over_remote_juggler(playwright, ws_endpoint)
        page = await browser.new_page()
        html = """
            <title>Remote Juggler</title>
            <main>
              <h1>Remote Juggler connected</h1>
              <button>Mark clicked</button>
              <pre id="async-stack">waiting</pre>
              <script>
                document.querySelector("button").addEventListener("click", () => {
                  document.body.setAttribute("data-clicked", "yes");
                });
                Promise.resolve().then(() => setTimeout(() => {
                  document.querySelector("#async-stack").textContent =
                    new Error("fingerprint-probe").stack;
                }, 0));
              </script>
            </main>
        """
        await page.goto(f"data:text/html,{quote(html)}")
        assert await page.title() == "Remote Juggler"
        await expect(page.locator("h1")).to_have_text("Remote Juggler connected")
        await page.locator("button").click()
        await expect(page.locator("body")).to_have_attribute("data-clicked", "yes")

        # A page-owned async chain reproduces Fingerprint's debugger probe; Juggler
        # must not add debugger-maintained parent frames to the content-visible stack.
        stack = page.locator("#async-stack")
        await expect(stack).not_to_have_text("waiting")
        assert not re.search(
            r"^(?:promise callback|setTimeout handler)\*",
            await stack.inner_text(),
            re.M,
        )
        assert len(await page.screenshot()) > 0

        await browser.close()
        browser = None
        await asyncio.wait_for(process.wait(), timeout=10)
        assert process.returncode == 0
    finally:
        if browser is not None and browser.is_connected():
            await browser.close()
        await _terminate_process(process)
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
