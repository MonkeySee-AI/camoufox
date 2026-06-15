from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright._impl._errors import Error as PlaywrightError
from playwright._impl._js_handle import parse_result, serialize_argument

from .assets import get_asset_by_name

_PATCH_ASSET_NAME = "playwrightUtilityEvalPatch.js"
_PATCH_INSTALLED = False


def install_utility_eval_driver_patch() -> Path:
    """
    Install Rotunda's Playwright driver preload for utility-world eval calls.

    This must run before Playwright starts its driver subprocess. Importing
    `rotunda` does that automatically for normal use; call this directly only
    when a process starts Playwright before importing the top-level package.
    """
    global _PATCH_INSTALLED

    patch_path = get_asset_by_name(_PATCH_ASSET_NAME)
    if _PATCH_INSTALLED:
        return patch_path

    import playwright._impl._driver as driver
    import playwright._impl._transport as transport

    original_get_driver_env = driver.get_driver_env
    preload_option = f"--require={patch_path}"

    def get_driver_env_with_utility_eval() -> dict[str, str]:
        env = original_get_driver_env()
        existing = env.get("NODE_OPTIONS", "")
        if preload_option not in existing.split():
            env["NODE_OPTIONS"] = f"{preload_option} {existing}".strip()
        return env

    vars(driver)["get_driver_env"] = get_driver_env_with_utility_eval
    vars(transport)["get_driver_env"] = get_driver_env_with_utility_eval
    _PATCH_INSTALLED = True
    return patch_path


def evaluate_in_utility(target: Any, expression: str, arg: Any = None) -> Any:
    """
    Evaluate JavaScript in Playwright's hidden utility world for a sync Page/Frame.

    `target` may be a Playwright sync `Page` or `Frame`. The expression and arg
    use the same serialization rules as Playwright's `evaluate`.
    """
    from playwright._impl._sync_base import mapping

    sync_runner = _sync_runner_from_target(target)
    frame = _frame_impl_from_target(target)
    result = sync_runner(
        _evaluate_in_utility_impl(frame, expression, mapping.to_impl(arg))
    )
    return mapping.from_maybe_impl(result)


async def async_evaluate_in_utility(
    target: Any, expression: str, arg: Any = None
) -> Any:
    """
    Evaluate JavaScript in Playwright's hidden utility world for an async Page/Frame.

    `target` may be a Playwright async `Page` or `Frame`. The expression and arg
    use the same serialization rules as Playwright's `evaluate`.
    """
    from playwright._impl._async_base import mapping

    frame = _frame_impl_from_target(target)
    result = await _evaluate_in_utility_impl(frame, expression, mapping.to_impl(arg))
    return mapping.from_maybe_impl(result)


async def _evaluate_in_utility_impl(frame: Any, expression: str, arg: Any) -> Any:
    try:
        return parse_result(
            await frame._channel.send(
                "rotundaEvaluateInUtility",
                None,
                {
                    "expression": expression,
                    "arg": serialize_argument(arg),
                },
            )
        )
    except PlaywrightError as error:
        _raise_if_missing_driver_patch(error, "rotundaEvaluateInUtility")
        raise


def _frame_impl_from_target(target: Any) -> Any:
    impl = getattr(target, "_impl_obj", target)
    frame = getattr(impl, "_main_frame", impl)
    if not hasattr(frame, "_channel"):
        raise TypeError("utility eval target must be a Playwright Page or Frame")
    return frame


def _sync_runner_from_target(target: Any) -> Any:
    sync_runner = getattr(target, "_sync", None)
    if not callable(sync_runner):
        raise TypeError(
            "sync utility eval expects a Playwright sync Page or Frame; "
            "use async_evaluate_in_utility with async Page/Frame objects"
        )
    return sync_runner


def _raise_if_missing_driver_patch(error: PlaywrightError, method: str) -> None:
    message = str(error)
    if method not in message:
        return
    if "Unknown scheme" not in message and "does not implement" not in message:
        return
    raise RuntimeError(
        "Rotunda utility eval requires its Playwright driver preload. Import "
        "`rotunda` or call `install_utility_eval_driver_patch()` before starting "
        "sync_playwright()/async_playwright(), then restart the Playwright connection."
    ) from error


install_utility_eval_driver_patch()


__all__ = [
    "async_evaluate_in_utility",
    "evaluate_in_utility",
    "install_utility_eval_driver_patch",
]
