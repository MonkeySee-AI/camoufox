from pathlib import Path


ROOT = Path(__file__).parents[1]
JUGGLER = ROOT / "browserbuild/additions/juggler"


def test_firefox_154_juggler_apis_are_preserved() -> None:
    runtime = (JUGGLER / "content/Runtime.js").read_text()
    frame_tree = (JUGGLER / "content/FrameTree.js").read_text()
    page_handler = (JUGGLER / "protocol/PageHandler.js").read_text()
    target_registry = (JUGGLER / "TargetRegistry.js").read_text()
    actor = (JUGGLER / "components/Juggler.js").read_text()

    # Firefox 154 removed these legacy hooks and globals; keep every Juggler
    # entry point on the replacement APIs exercised by the browser smoke test.
    assert "onPromiseSettled" not in runtime
    assert "Promise.race([obj.unsafeDereference(), contextDestroyed])" in runtime
    assert "event.target.documentGlobal" in frame_tree
    assert "browsingContext.topChromeWindow;" in page_handler
    assert "this._tab.linkedBrowser.documentGlobal" in target_registry
    assert "safeForUntrustedWebProcess: true" in actor
