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

    page_agent = (JUGGLER / "content/PageAgent.js").read_text()
    assert "win.synthesizeMouseEvent(type, x, y" in page_agent
    assert "toWindow: true" in page_agent


def test_firefox_154_stock_javascript_surfaces_are_enabled() -> None:
    settings = (ROOT / "browserbuild/settings/rotunda.cfg").read_text()
    policies = (ROOT / "browserbuild/settings/distribution/policies.json").read_text()

    # These stock desktop surfaces must not be hidden by Rotunda's pinned prefs.
    assert 'defaultPref("dom.documentpip.enabled", true);' in settings
    assert '"DefaultSerialGuardSetting": 3' in policies
