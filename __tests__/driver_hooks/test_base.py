from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from playwright._impl._errors import Error as PlaywrightError

import rotunda.driver_hooks.base as driver_hooks
from rotunda.driver_hooks.base import (
    install_playwright_driver_hooks,
    raise_if_missing_playwright_driver_hook,
    register_playwright_driver_hook,
    registered_playwright_driver_hooks,
)


@contextmanager
def temporary_driver_hook_registry() -> Iterator[None]:
    original_hooks = dict(driver_hooks._DRIVER_HOOKS)
    try:
        yield
    finally:
        driver_hooks._DRIVER_HOOKS.clear()
        driver_hooks._DRIVER_HOOKS.update(original_hooks)


def test_driver_hook_registry_installs_hooks_registered_after_install(
    tmp_path: Path,
) -> None:
    baseline_hooks = registered_playwright_driver_hooks()
    with temporary_driver_hook_registry():
        install_playwright_driver_hooks()
        hook_path = tmp_path / "extra-driver-hook.js"
        hook_path.write_text('"use strict";\n')

        # Hooks may be registered after the env wrapper is installed; future driver
        # subprocesses still need to see the updated registry.
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

    assert registered_playwright_driver_hooks() == baseline_hooks
    assert f"--require={registered}" not in transport.get_driver_env()[
        "NODE_OPTIONS"
    ].split()


def test_missing_driver_hook_error_is_actionable_for_unknown_schema() -> None:
    with pytest.raises(RuntimeError, match="Rotunda isolated eval requires"):
        raise_if_missing_playwright_driver_hook(
            PlaywrightError("Unknown scheme for Params: Frame.rotundaEvaluateInUtility"),
            method="rotundaEvaluateInUtility",
            feature="isolated eval",
        )


def test_missing_driver_hook_error_is_actionable_for_unimplemented_method() -> None:
    with pytest.raises(RuntimeError, match="install_playwright_driver_hooks"):
        raise_if_missing_playwright_driver_hook(
            PlaywrightError(
                'Dispatcher "Frame" does not implement "rotundaEvaluateInUtility"'
            ),
            method="rotundaEvaluateInUtility",
            feature="isolated eval",
        )


def test_missing_driver_hook_error_ignores_unrelated_playwright_errors() -> None:
    raise_if_missing_playwright_driver_hook(
        PlaywrightError("Unknown scheme for Params: Other.method"),
        method="rotundaEvaluateInUtility",
        feature="isolated eval",
    )
    raise_if_missing_playwright_driver_hook(
        PlaywrightError("Frame.rotundaEvaluateInUtility timed out"),
        method="rotundaEvaluateInUtility",
        feature="isolated eval",
    )
