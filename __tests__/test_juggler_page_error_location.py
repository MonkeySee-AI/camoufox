from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
JUGGLER_ROOT = REPO_ROOT / "additions" / "juggler"


def test_page_uncaught_error_protocol_declares_location() -> None:
    protocol = (JUGGLER_ROOT / "protocol" / "Protocol.js").read_text(encoding="utf-8")

    assert re.search(
        r"'uncaughtError': \{[^}]*location: runtimeTypes\.ScriptLocation",
        protocol,
        re.DOTALL,
    )


def test_page_uncaught_error_producer_emits_location() -> None:
    runtime = (JUGGLER_ROOT / "content" / "Runtime.js").read_text(encoding="utf-8")
    page_agent = (JUGGLER_ROOT / "content" / "PageAgent.js").read_text(encoding="utf-8")

    assert "function scriptLocationFromConsoleMessage(message)" in runtime
    assert "location: scriptLocationFromConsoleMessage(message)" in runtime
    assert re.search(
        r"onErrorFromWorker\(\(\{ domWindow, message, stack, location \}\) => \{.*?pageUncaughtError.*?location,",
        page_agent,
        re.DOTALL,
    )
    assert re.search(
        r"_onRuntimeError\(\{ executionContext, message, stack, location \}\) \{.*?pageUncaughtError.*?location,",
        page_agent,
        re.DOTALL,
    )
