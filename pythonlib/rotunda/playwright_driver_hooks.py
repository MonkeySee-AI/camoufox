from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .assets import get_asset_by_name

_DRIVER_HOOKS: dict[str, PlaywrightDriverHook] = {}
_HOOKS_INSTALLED = False


@dataclass(frozen=True)
class PlaywrightDriverHook:
    name: str
    preload: Path


def register_playwright_driver_hook(name: str, preload: str | Path) -> Path:
    """
    Register a Node preload that patches Playwright's JavaScript driver process.

    `preload` can be a packaged Rotunda asset name or an absolute path. Registered
    hooks are installed into future Playwright driver subprocesses by
    `install_playwright_driver_hooks()`.
    """
    preload_path = get_asset_by_name(preload) if isinstance(preload, str) else preload
    preload_path = preload_path.resolve()
    if not preload_path.is_file():
        raise FileNotFoundError(f"Playwright driver hook is missing: {preload_path}")

    existing = _DRIVER_HOOKS.get(name)
    if existing:
        if existing.preload != preload_path:
            raise ValueError(
                f'Playwright driver hook "{name}" is already registered for {existing.preload}'
            )
        return existing.preload

    _DRIVER_HOOKS[name] = PlaywrightDriverHook(name=name, preload=preload_path)
    return preload_path


def registered_playwright_driver_hooks() -> tuple[PlaywrightDriverHook, ...]:
    return tuple(_DRIVER_HOOKS.values())


def install_playwright_driver_hooks() -> tuple[Path, ...]:
    """
    Install all registered Rotunda Playwright driver hooks.

    This must run before Playwright starts its driver subprocess. The wrapper is
    idempotent and reads the hook registry each time Playwright asks for a driver
    environment, so hooks registered after installation still apply to future
    driver subprocesses.
    """
    global _HOOKS_INSTALLED

    if _HOOKS_INSTALLED:
        return _registered_preloads()

    import playwright._impl._driver as driver
    import playwright._impl._transport as transport

    original_get_driver_env = driver.get_driver_env

    def get_driver_env_with_rotunda_hooks() -> dict[str, str]:
        env = original_get_driver_env()
        env["NODE_OPTIONS"] = _node_options_with_hooks(env.get("NODE_OPTIONS", ""))
        return env

    vars(driver)["get_driver_env"] = get_driver_env_with_rotunda_hooks
    vars(transport)["get_driver_env"] = get_driver_env_with_rotunda_hooks
    _HOOKS_INSTALLED = True
    return _registered_preloads()


def _registered_preloads() -> tuple[Path, ...]:
    return tuple(hook.preload for hook in _DRIVER_HOOKS.values())


def _node_options_with_hooks(existing: str) -> str:
    options = existing.split()
    registered_options = [f"--require={preload}" for preload in _registered_preloads()]
    for option in reversed(registered_options):
        if option not in options:
            options.insert(0, option)
    return " ".join(options)


__all__ = [
    "PlaywrightDriverHook",
    "install_playwright_driver_hooks",
    "register_playwright_driver_hook",
    "registered_playwright_driver_hooks",
]
