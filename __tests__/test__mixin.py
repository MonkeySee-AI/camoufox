from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from _mixin import get_moz_target  # noqa: E402


def test_x86_64_linux_uses_rust_target_vendor() -> None:
    assert get_moz_target("linux", "x86_64") == "x86_64-unknown-linux-gnu"
