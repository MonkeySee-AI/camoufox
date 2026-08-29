from pathlib import Path

ROOT = Path(__file__).parents[1]
SHADOW_ROOT_PATCH = ROOT / "browserbuild/patches/shadow-root-bypass.patch"


def test_closed_shadow_root_chrome_alias_is_preserved() -> None:
    patch = SHADOW_ROOT_PATCH.read_text()

    assert 'BinaryName="openOrClosedShadowRoot"' in patch
    assert "readonly attribute ShadowRoot? shadowRootUnl;" in patch
