# Copyright (c) 2026 Pierce Freeman.

import asyncio
import base64
import json
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any

import orjson

from rotunda.assets import get_asset_by_name
from rotunda.driver_hooks import install_playwright_driver_hooks
from rotunda.server import get_nodejs

BRIDGE_SCRIPT: Path = get_asset_by_name("connectRemoteJuggler.js")


def _bridge_env() -> dict[str, str]:
    install_playwright_driver_hooks()
    from playwright._impl import _driver

    return _driver.get_driver_env()


def _bridge_payload(
    endpoint: str,
    *,
    slow_mo: float | None = None,
    headers: dict[str, str] | None = None,
    firefox_user_prefs: dict[str, str | float | bool] | None = None,
    downloads_path: str | Path | None = None,
    traces_dir: str | Path | None = None,
    attach_to_default_context: bool = True,
    server_host: str = "127.0.0.1",
    server_port: int = 0,
    ws_path: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "endpoint": endpoint,
        "attachToDefaultContext": attach_to_default_context,
        "serverHost": server_host,
        "serverPort": server_port,
    }
    if slow_mo is not None:
        payload["slowMo"] = slow_mo
    if headers:
        payload["headers"] = headers
    if firefox_user_prefs:
        payload["firefoxUserPrefs"] = firefox_user_prefs
    if downloads_path is not None:
        payload["downloadsPath"] = str(downloads_path)
    if traces_dir is not None:
        payload["tracesDir"] = str(traces_dir)
    if ws_path is not None:
        payload["wsPath"] = ws_path
    return payload


def _encoded_payload(payload: dict[str, Any]) -> str:
    return base64.b64encode(orjson.dumps(payload)).decode()


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _read_stdout_line(
    process: subprocess.Popen[str],
    *,
    timeout: float | None,
) -> str:
    if process.stdout is None:
        raise RuntimeError("Remote Juggler bridge did not expose stdout.")
    stdout = process.stdout

    lines: queue.Queue[str] = queue.Queue(maxsize=1)

    def read_line() -> None:
        lines.put(stdout.readline())

    thread = threading.Thread(target=read_line, daemon=True)
    thread.start()
    try:
        line = lines.get(timeout=timeout)
    except queue.Empty as exc:
        _terminate_process(process)
        raise TimeoutError("Timed out waiting for remote Juggler bridge to start.") from exc

    if line:
        return line

    stderr = ""
    if process.stderr is not None:
        stderr = process.stderr.read()
    raise RuntimeError(f"Remote Juggler bridge exited before reporting an endpoint. {stderr}".strip())


def _start_bridge(
    payload: dict[str, Any],
    *,
    timeout: float | None,
) -> tuple[subprocess.Popen[str], str]:
    nodejs = get_nodejs()
    process = subprocess.Popen(  # nosec
        [nodejs, str(BRIDGE_SCRIPT)],
        cwd=Path(nodejs).parent / "package",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_bridge_env(),
    )
    assert process.stdin is not None
    process.stdin.write(_encoded_payload(payload))
    process.stdin.close()

    timeout_s = None if timeout is None else timeout / 1000
    line = _read_stdout_line(process, timeout=timeout_s)
    try:
        data = json.loads(line)
        return process, data["wsEndpoint"]
    except Exception as exc:
        _terminate_process(process)
        raise RuntimeError(
            f"Remote Juggler bridge returned invalid startup data: {line!r}"
        ) from exc


async def _terminate_async_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=5)


async def _start_bridge_async(
    payload: dict[str, Any],
    *,
    timeout: float | None,
) -> tuple[asyncio.subprocess.Process, str]:
    nodejs = get_nodejs()
    process = await asyncio.create_subprocess_exec(
        nodejs,
        str(BRIDGE_SCRIPT),
        cwd=Path(nodejs).parent / "package",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_bridge_env(),
    )
    assert process.stdin is not None
    process.stdin.write(_encoded_payload(payload).encode())
    await process.stdin.drain()
    process.stdin.close()

    assert process.stdout is not None
    timeout_s = None if timeout is None else timeout / 1000
    try:
        raw_line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        await _terminate_async_process(process)
        raise TimeoutError("Timed out waiting for remote Juggler bridge to start.") from exc

    if not raw_line:
        stderr = ""
        if process.stderr is not None:
            stderr = (await process.stderr.read()).decode()
        raise RuntimeError(
            f"Remote Juggler bridge exited before reporting an endpoint. {stderr}".strip()
        )

    try:
        data = json.loads(raw_line.decode())
        return process, data["wsEndpoint"]
    except Exception as exc:
        await _terminate_async_process(process)
        raise RuntimeError(
            f"Remote Juggler bridge returned invalid startup data: {raw_line!r}"
        ) from exc


def connect_over_remote_juggler(
    playwright: Any,
    endpoint: str,
    *,
    timeout: float | None = 30_000,
    slow_mo: float | None = None,
    headers: dict[str, str] | None = None,
    firefox_user_prefs: dict[str, str | float | bool] | None = None,
    downloads_path: str | Path | None = None,
    traces_dir: str | Path | None = None,
    attach_to_default_context: bool = True,
    server_host: str = "127.0.0.1",
    server_port: int = 0,
    ws_path: str | None = None,
) -> Any:
    payload = _bridge_payload(
        endpoint,
        slow_mo=slow_mo,
        headers=headers,
        firefox_user_prefs=firefox_user_prefs,
        downloads_path=downloads_path,
        traces_dir=traces_dir,
        attach_to_default_context=attach_to_default_context,
        server_host=server_host,
        server_port=server_port,
        ws_path=ws_path,
    )
    process, ws_endpoint = _start_bridge(payload, timeout=timeout)
    try:
        browser = playwright.firefox.connect(ws_endpoint, timeout=timeout, slow_mo=slow_mo)
    except Exception:
        _terminate_process(process)
        raise

    close = browser.close

    def wrapped_close(*args: Any, **kwargs: Any) -> Any:
        try:
            return close(*args, **kwargs)
        finally:
            _terminate_process(process)

    browser.close = wrapped_close
    browser._remote_juggler_bridge_process = process
    browser._remote_juggler_playwright_endpoint = ws_endpoint
    return browser


async def async_connect_over_remote_juggler(
    playwright: Any,
    endpoint: str,
    *,
    timeout: float | None = 30_000,
    slow_mo: float | None = None,
    headers: dict[str, str] | None = None,
    firefox_user_prefs: dict[str, str | float | bool] | None = None,
    downloads_path: str | Path | None = None,
    traces_dir: str | Path | None = None,
    attach_to_default_context: bool = True,
    server_host: str = "127.0.0.1",
    server_port: int = 0,
    ws_path: str | None = None,
) -> Any:
    payload = _bridge_payload(
        endpoint,
        slow_mo=slow_mo,
        headers=headers,
        firefox_user_prefs=firefox_user_prefs,
        downloads_path=downloads_path,
        traces_dir=traces_dir,
        attach_to_default_context=attach_to_default_context,
        server_host=server_host,
        server_port=server_port,
        ws_path=ws_path,
    )
    process, ws_endpoint = await _start_bridge_async(payload, timeout=timeout)
    try:
        browser = await playwright.firefox.connect(ws_endpoint, timeout=timeout, slow_mo=slow_mo)
    except Exception:
        await _terminate_async_process(process)
        raise

    close = browser.close

    async def wrapped_close(*args: Any, **kwargs: Any) -> Any:
        try:
            return await close(*args, **kwargs)
        finally:
            await _terminate_async_process(process)

    browser.close = wrapped_close
    browser._remote_juggler_bridge_process = process
    browser._remote_juggler_playwright_endpoint = ws_endpoint
    return browser


ConnectOverRemoteJuggler = connect_over_remote_juggler
AsyncConnectOverRemoteJuggler = async_connect_over_remote_juggler
