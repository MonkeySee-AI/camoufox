from unittest.mock import MagicMock

from rotunda import remote_juggler
from rotunda.driver_hooks import registered_playwright_driver_hooks


def test_remote_bridge_inherits_playwright_driver_hooks(monkeypatch) -> None:
    """Load Rotunda protocol extensions in the remote-Juggler bridge process."""
    # Avoid starting Node while retaining the exact subprocess arguments.
    process = MagicMock()
    process.stdin = MagicMock()
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(remote_juggler.subprocess, "Popen", popen)
    monkeypatch.setattr(
        remote_juggler,
        "_read_stdout_line",
        lambda *_args, **_kwargs: '{"wsEndpoint":"ws://bridge"}',
    )

    returned_process, endpoint = remote_juggler._start_bridge({}, timeout=1)

    # Every registered preload must reach the child that validates protocols.
    node_options = popen.call_args.kwargs["env"]["NODE_OPTIONS"].split()
    assert returned_process is process
    assert endpoint == "ws://bridge"
    assert {
        f"--require={hook.preload}" for hook in registered_playwright_driver_hooks()
    }.issubset(node_options)
