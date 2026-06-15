from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

import playwright
import pytest
from playwright._impl._errors import Error as PlaywrightError
from playwright._impl._js_handle import parse_value

from rotunda.assets import get_asset_by_name
from rotunda.playwright_driver_hooks import (
    install_playwright_driver_hooks,
    register_playwright_driver_hook,
    registered_playwright_driver_hooks,
)
from rotunda.utility_eval import (
    async_evaluate_in_utility,
    evaluate_in_utility,
    install_utility_eval_driver_patch,
)


class FakeChannel:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.response = {"n": 7} if response is None else response
        self.error = error

    async def send(self, method: str, timeout: Any, params: dict[str, Any]) -> Any:
        self.calls.append((method, timeout, params))
        if self.error:
            raise self.error
        return self.response


class FakeFrame:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel


class FakePageImpl:
    def __init__(self, frame: FakeFrame) -> None:
        self._main_frame = frame


class FakeAsyncPage:
    def __init__(self, frame: FakeFrame) -> None:
        self._impl_obj = FakePageImpl(frame)


class FakeSyncPage(FakeAsyncPage):
    def _sync(self, coro: Any) -> Any:
        return asyncio.run(coro)


def test_install_utility_eval_driver_patch_adds_node_preload() -> None:
    patch_path = install_utility_eval_driver_patch()

    import playwright._impl._transport as transport

    env = transport.get_driver_env()
    assert f"--require={patch_path}" in env["NODE_OPTIONS"].split()


def test_driver_hook_registry_installs_hooks_registered_after_install(
    tmp_path: Path,
) -> None:
    hook_path = tmp_path / "extra-driver-hook.js"
    hook_path.write_text('"use strict";\n')

    registered = register_playwright_driver_hook("test-extra-hook", hook_path)
    installed = install_playwright_driver_hooks()

    import playwright._impl._transport as transport

    env = transport.get_driver_env()
    assert registered == hook_path.resolve()
    assert registered in installed
    assert f"--require={registered}" in env["NODE_OPTIONS"].split()
    assert any(
        hook.name == "test-extra-hook" and hook.preload == registered
        for hook in registered_playwright_driver_hooks()
    )


def test_playwright_utility_eval_patch_adds_dispatcher_methods_and_schema() -> None:
    from playwright._impl._driver import compute_driver_executable

    node, _ = compute_driver_executable()
    patch_path = get_asset_by_name("playwrightUtilityEvalPatch.js")
    driver_lib = (
        Path(playwright.__file__).resolve().parent / "driver" / "package" / "lib"
    )
    script = f"""
const base = {str(driver_lib)!r};
require(base + "/protocol/validator.js");
const {{ maybeFindValidator }} = require(base + "/protocol/validatorPrimitives.js");
const {{ FrameDispatcher }} = require(base + "/server/dispatchers/frameDispatcher.js");
const paramsValidator = maybeFindValidator("Frame", "rotundaEvaluateInUtility", "Params");
const resultValidator = maybeFindValidator("Frame", "rotundaEvaluateInUtility", "Result");
if (!paramsValidator || !resultValidator)
  throw new Error("missing utility eval validator");
if (typeof FrameDispatcher.prototype.rotundaEvaluateInUtility !== "function")
  throw new Error("missing value utility eval dispatcher");
const validated = paramsValidator(
  {{ expression: "() => 1", arg: {{ value: {{ n: 3 }}, handles: [] }} }},
  "",
  {{ binary: "fromBase64", isUnderTest: () => false, tChannelImpl: () => null }}
);
if (validated.expression !== "() => 1")
  throw new Error("validator did not preserve expression");
"""
    env = os.environ.copy()
    env["NODE_OPTIONS"] = (
        f"--require={patch_path} {env.get('NODE_OPTIONS', '')}".strip()
    )
    subprocess.run([node, "-e", script], check=True, env=env)


@pytest.mark.asyncio
async def test_async_evaluate_in_utility_sends_rotunda_protocol_method() -> None:
    channel = FakeChannel(response={"n": 9})
    page = FakeAsyncPage(FakeFrame(channel))

    result = await async_evaluate_in_utility(page, "arg => arg.count", {"count": 9})

    assert result == 9
    assert len(channel.calls) == 1
    method, timeout, params = channel.calls[0]
    assert method == "rotundaEvaluateInUtility"
    assert timeout is None
    assert params["expression"] == "arg => arg.count"
    assert parse_value(params["arg"]["value"]) == {"count": 9}
    assert params["arg"]["handles"] == []


def test_sync_evaluate_in_utility_sends_rotunda_protocol_method() -> None:
    channel = FakeChannel(response={"s": "utility"})
    page = FakeSyncPage(FakeFrame(channel))

    result = evaluate_in_utility(page, "() => 'utility'")

    assert result == "utility"
    assert channel.calls[0][0] == "rotundaEvaluateInUtility"


@pytest.mark.asyncio
async def test_missing_driver_patch_error_is_actionable() -> None:
    channel = FakeChannel(
        error=PlaywrightError(
            "Unknown scheme for Params: Frame.rotundaEvaluateInUtility"
        )
    )
    page = FakeAsyncPage(FakeFrame(channel))

    with pytest.raises(RuntimeError, match="requires its Playwright driver preload"):
        await async_evaluate_in_utility(page, "() => 1")
