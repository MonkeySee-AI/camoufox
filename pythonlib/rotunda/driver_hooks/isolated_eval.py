from __future__ import annotations

from typing import Any

from playwright._impl._errors import Error as PlaywrightError
from playwright._impl._js_handle import parse_result, serialize_argument

from .base import (
    install_playwright_driver_hooks,
    raise_if_missing_playwright_driver_hook,
    register_playwright_driver_hook,
)

_PATCH_ASSET_NAME = "playwrightUtilityEvalPatch.js"
register_playwright_driver_hook("isolated_eval", _PATCH_ASSET_NAME)


def evaluate_in_utility(target: Any, expression: str, arg: Any = None) -> Any:
    """
    Evaluate JavaScript in Playwright's isolated utility context for a sync Page/Frame.

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
    Evaluate JavaScript in Playwright's isolated utility context for an async Page/Frame.

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
        raise_if_missing_playwright_driver_hook(
            error,
            method="rotundaEvaluateInUtility",
            feature="isolated eval",
        )
        raise


def _frame_impl_from_target(target: Any) -> Any:
    impl = getattr(target, "_impl_obj", target)
    frame = getattr(impl, "_main_frame", impl)
    if not hasattr(frame, "_channel"):
        raise TypeError("isolated eval target must be a Playwright Page or Frame")
    return frame


def _sync_runner_from_target(target: Any) -> Any:
    sync_runner = getattr(target, "_sync", None)
    if not callable(sync_runner):
        raise TypeError(
            "sync isolated eval expects a Playwright sync Page or Frame; "
            "use async_evaluate_in_utility with async Page/Frame objects"
        )
    return sync_runner


install_playwright_driver_hooks()


__all__ = [
    "async_evaluate_in_utility",
    "evaluate_in_utility",
]
