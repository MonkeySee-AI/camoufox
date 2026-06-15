from __future__ import annotations

from pathlib import Path

from rotunda.playwright_driver_hooks import (
    install_playwright_driver_hooks,
    register_playwright_driver_hook,
    registered_playwright_driver_hooks,
)


def test_driver_hook_registry_installs_hooks_registered_after_install(
    tmp_path: Path,
) -> None:
    install_playwright_driver_hooks()
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
