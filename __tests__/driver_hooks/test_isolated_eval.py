from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import playwright
import pytest
from playwright._impl._errors import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from __tests__.fixtures import get_fixtures_path
from rotunda.assets import get_asset_by_name
from rotunda.driver_hooks.base import (
    install_playwright_driver_hooks,
    registered_playwright_driver_hooks,
)
from rotunda.driver_hooks.isolated_eval import evaluate_in_utility

_INSTRUMENTED_DOM_READS_HTML = get_fixtures_path("html/instrumented_dom_reads.html")
_DOM_READ_EVAL = get_fixtures_path("js/dom_read_eval.js").read_text()
_ISOLATED_EVAL_PATCH_SENTINEL = get_fixtures_path("js/assert_isolated_eval_patch.js")


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


def _read_counters(page: Any) -> dict[str, int]:
    return page.evaluate("() => ({ ...window.__rotundaReadCounters })")


def _assert_counter_was_hit(counters: dict[str, int], name: str) -> None:
    assert counters.get(name, 0) > 0, counters


def _assert_counter_was_not_hit(counters: dict[str, int], name: str) -> None:
    assert counters.get(name, 0) == 0, counters


def test_isolated_eval_registers_driver_hook() -> None:
    patch_path = get_asset_by_name("playwrightUtilityEvalPatch.js")
    installed = install_playwright_driver_hooks()

    assert patch_path in installed
    assert any(
        hook.name == "isolated_eval" and hook.preload == patch_path
        for hook in registered_playwright_driver_hooks()
    )


def test_playwright_isolated_eval_patch_adds_dispatcher_methods_and_schema() -> None:
    from playwright._impl._driver import compute_driver_executable

    node, _ = compute_driver_executable()
    patch_path = get_asset_by_name("playwrightUtilityEvalPatch.js")
    driver_lib = (
        Path(playwright.__file__).resolve().parent / "driver" / "package" / "lib"
    )
    # This is a version-drift sentinel for the private Playwright modules our
    # preload patches; behavioral isolation is covered by the browser test below.
    env = os.environ.copy()
    env["NODE_OPTIONS"] = (
        f"--require={patch_path} {env.get('NODE_OPTIONS', '')}".strip()
    )
    subprocess.run(
        [node, str(_ISOLATED_EVAL_PATCH_SENTINEL), str(driver_lib)],
        check=True,
        env=env,
    )


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
            page.goto(_INSTRUMENTED_DOM_READS_HTML.as_uri(), wait_until="load")

            # The fixture counts page-visible DOM reads. Main-world eval should
            # trip those counters, while isolated eval should not.
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
    from rotunda.driver_hooks.isolated_eval import async_evaluate_in_utility

    channel = FakeChannel(
        PlaywrightError("Unknown scheme for Params: Frame.rotundaEvaluateInUtility")
    )
    page = FakeAsyncPage(FakeFrame(channel))

    with pytest.raises(RuntimeError, match="requires its Playwright driver preload"):
        await async_evaluate_in_utility(page, "() => 1")
