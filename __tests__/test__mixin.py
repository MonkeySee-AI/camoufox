from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import _mixin  # noqa: E402


def test_x86_64_linux_uses_rust_target_vendor() -> None:
    assert _mixin.get_moz_target("linux", "x86_64") == "x86_64-unknown-linux-gnu"


def test_linux_mozconfig_uses_the_rustc_host() -> None:
    mozconfig = SCRIPTS_ROOT.parent / "browserbuild" / "assets" / "linux.mozconfig"

    assert 'ac_add_options --host="$(rustc -vV' in mozconfig.read_text()


def test_patch_prefers_gnu_patch(monkeypatch) -> None:
    commands = []
    monkeypatch.setattr(_mixin.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(_mixin, "run", commands.append)

    _mixin.patch("feature.patch")

    assert commands == ["/bin/gpatch -p1 -i feature.patch"]
