from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

import playwright
import pytest
from playwright._impl._errors import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from rotunda.assets import get_asset_by_name
from rotunda.playwright_driver_hooks import (
    install_playwright_driver_hooks,
    register_playwright_driver_hook,
    registered_playwright_driver_hooks,
)
from rotunda.utility_eval import (
    evaluate_in_utility,
    install_utility_eval_driver_patch,
)


class FakeChannel:
    def __init__(self, error: Exception) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.error = error

    async def send(self, method: str, timeout: Any, params: dict[str, Any]) -> Any:
        self.calls.append((method, timeout, params))
        raise self.error


class FakeFrame:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel


class FakePageImpl:
    def __init__(self, frame: FakeFrame) -> None:
        self._main_frame = frame


class FakeAsyncPage:
    def __init__(self, frame: FakeFrame) -> None:
        self._impl_obj = FakePageImpl(frame)


_INSTRUMENTED_DOM_READS_HTML = """
<!doctype html>
<html>
<head>
  <script>
    (() => {
      const counts = Object.create(null);
      const count = name => counts[name] = (counts[name] || 0) + 1;
      const wrap = (owner, name, label) => {
        const original = owner[name];
        Object.defineProperty(owner, name, {
          configurable: true,
          writable: true,
          value: function(...args) {
            count(label);
            return original.apply(this, args);
          },
        });
      };
      Object.defineProperty(window, "__rotundaReadCounters", { value: counts });
      Object.defineProperty(window, "__resetRotundaReadCounters", {
        value: () => {
          for (const key of Object.keys(counts))
            delete counts[key];
        },
      });
      wrap(Document.prototype, "querySelector", "Document.querySelector");
      wrap(Document.prototype, "querySelectorAll", "Document.querySelectorAll");
      wrap(Element.prototype, "getAttribute", "Element.getAttribute");
      wrap(Element.prototype, "getBoundingClientRect", "Element.getBoundingClientRect");
      wrap(Element.prototype, "closest", "Element.closest");
      const originalGetComputedStyle = window.getComputedStyle;
      Object.defineProperty(window, "getComputedStyle", {
        configurable: true,
        writable: true,
        value: function(...args) {
          count("window.getComputedStyle");
          return originalGetComputedStyle.apply(this, args);
        },
      });
    })();
  </script>
</head>
<body>
  <main data-root>
    <div id="target" class="item" data-name="target" style="width: 17px">hello</div>
    <div class="item">world</div>
  </main>
</body>
</html>
"""

_DOM_READ_EVAL = """
() => {
  const root = document.querySelector("[data-root]");
  const allItems = document.querySelectorAll(".item");
  const target = document.querySelector("#target");
  const style = window.getComputedStyle(target);
  const rect = target.getBoundingClientRect();
  return {
    itemCount: allItems.length,
    id: target.getAttribute("id"),
    closestRoot: target.closest("[data-root]") === root,
    display: style.display,
    width: rect.width,
  };
}
"""


def _read_counters(page: Any) -> dict[str, int]:
    return page.evaluate("() => ({ ...window.__rotundaReadCounters })")


def _assert_counter_was_hit(counters: dict[str, int], name: str) -> None:
    assert counters.get(name, 0) > 0, counters


def _assert_counter_was_not_hit(counters: dict[str, int], name: str) -> None:
    assert counters.get(name, 0) == 0, counters


def test_utility_eval_registers_driver_hook() -> None:
    patch_path = install_utility_eval_driver_patch()

    assert any(
        hook.name == "isolated_eval" and hook.preload == patch_path
        for hook in registered_playwright_driver_hooks()
    )


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
    assert f"--require={get_asset_by_name('playwrightUtilityEvalPatch.js')}" in env[
        "NODE_OPTIONS"
    ].split()
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


@pytest.mark.integration
def test_isolated_eval_does_not_trip_page_shadowed_dom_reads(
    pytestconfig: pytest.Config,
) -> None:
    if not pytestconfig.getoption("--integration"):
        pytest.skip("pass --integration to run browser integration coverage")

    with sync_playwright() as playwright_instance:
        try:
            browser = playwright_instance.firefox.launch(headless=True)
        except Exception as error:
            text = str(error)
            if "Executable doesn't exist" in text or "Please run" in text:
                pytest.skip("Playwright Firefox is not installed")
            raise

        try:
            page = browser.new_page()
            page.goto(
                "data:text/html," + quote(_INSTRUMENTED_DOM_READS_HTML),
                wait_until="load",
            )

            page.evaluate("() => window.__resetRotundaReadCounters()")
            main_world_result = page.evaluate(_DOM_READ_EVAL)
            main_world_counters = _read_counters(page)

            page.evaluate("() => window.__resetRotundaReadCounters()")
            isolated_result = evaluate_in_utility(page, _DOM_READ_EVAL)
            isolated_counters = _read_counters(page)
        finally:
            browser.close()

    assert isolated_result == main_world_result
    for name in [
        "Document.querySelector",
        "Document.querySelectorAll",
        "window.getComputedStyle",
        "Element.getBoundingClientRect",
        "Element.getAttribute",
        "Element.closest",
    ]:
        _assert_counter_was_hit(main_world_counters, name)
        _assert_counter_was_not_hit(isolated_counters, name)


@pytest.mark.asyncio
async def test_missing_driver_patch_error_is_actionable() -> None:
    from rotunda.utility_eval import async_evaluate_in_utility

    channel = FakeChannel(
        PlaywrightError("Unknown scheme for Params: Frame.rotundaEvaluateInUtility")
    )
    page = FakeAsyncPage(FakeFrame(channel))

    with pytest.raises(RuntimeError, match="requires its Playwright driver preload"):
        await async_evaluate_in_utility(page, "() => 1")
