from __future__ import annotations

import os
import subprocess
from pathlib import Path

import playwright
from rotunda.assets import get_asset_by_name
from rotunda.driver_hooks.base import (
    install_playwright_driver_hooks,
    registered_playwright_driver_hooks,
)

from __tests__.fixtures import get_fixtures_path

_PATCH_SENTINEL = get_fixtures_path("js/assert_element_screencast_patch.js")


def test_element_screencast_registers_driver_hook() -> None:
    patch_path = get_asset_by_name("playwrightElementScreencastPatch.js")
    installed = install_playwright_driver_hooks()

    assert patch_path in installed
    assert any(
        hook.name == "element_screencast" and hook.preload == patch_path
        for hook in registered_playwright_driver_hooks()
    )


def test_element_screencast_patch_matches_playwright_driver_contract() -> None:
    from playwright._impl._driver import compute_driver_executable

    node, _ = compute_driver_executable()
    patch_path = get_asset_by_name("playwrightElementScreencastPatch.js")
    driver_lib = (
        Path(playwright.__file__).resolve().parent / "driver" / "package" / "lib"
    )
    # This sentinel proves the private schema and dispatcher entry points still
    # exist; the Rotunda browser test owns actual resize/offscreen behavior.
    env = os.environ.copy()
    env["NODE_OPTIONS"] = (
        f"--require={patch_path} {env.get('NODE_OPTIONS', '')}".strip()
    )
    subprocess.run(
        [node, str(_PATCH_SENTINEL), str(driver_lib)],
        check=True,
        env=env,
    )
