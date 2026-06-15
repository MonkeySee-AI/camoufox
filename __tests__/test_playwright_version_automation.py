from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import resolve_playwright_versions as resolver  # noqa: E402


def _installable_files() -> list[dict[str, object]]:
    return [{"filename": "playwright.whl", "yanked": False}]


def _load_update_script():
    script_path = REPO_ROOT / ".github" / "scripts" / "update-playwright-max-version.py"
    spec = importlib.util.spec_from_file_location(
        "update_playwright_max_version", script_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_playwright_versions_honors_maximum_version() -> None:
    releases = {
        "1.51.0": _installable_files(),
        "1.51.1": _installable_files(),
        "1.52.0": _installable_files(),
        "1.60.0": _installable_files(),
        "1.60.1": _installable_files(),
        "1.61.0": _installable_files(),
    }

    assert resolver.select_recent_minor_versions(
        releases,
        limit=10,
        minimum_version="1.51.0",
        maximum_version="1.60.0",
    ) == ["1.51.1", "1.52.0", "1.60.0"]


def test_read_playwright_bounds_uses_project_dependencies(tmp_path: Path) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        """\
[project]
keywords = [
    "playwright",
]
dependencies = [
    "requests",
    "playwright>=1.51.0,<=1.59.0",
]
""",
        encoding="utf-8",
    )

    assert resolver.read_playwright_version_bounds(pyproject_path) == (
        "1.51.0",
        "1.59.0",
    )


def test_update_playwright_max_preserves_minimum_bound(tmp_path: Path) -> None:
    update_script = _load_update_script()
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        """\
[project]
dependencies = [
    "playwright>=1.51.0,<=1.59.0",
]
""",
        encoding="utf-8",
    )

    assert update_script.update_playwright_max(pyproject_path, "1.60.0") == (
        "1.51.0",
        "1.59.0",
    )
    assert '"playwright>=1.51.0,<=1.60.0",' in pyproject_path.read_text(
        encoding="utf-8"
    )
