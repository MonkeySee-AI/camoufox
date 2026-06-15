#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "click>=8.1",
# ]
# ///

"""Update Rotunda's validated Playwright upper bound for the automation workflow.

The script reads the Playwright dependency from ``pythonlib/pyproject.toml``,
finds the latest stable, non-yanked Playwright release on PyPI, and compares it
with Rotunda's current ``<=`` maximum. When a newer release exists, it can patch
only that upper bound in place and emit GitHub Actions outputs for the branch
name, PR title, old max version, latest version, and whether a PR is needed.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import click


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from resolve_playwright_versions import read_playwright_version_bounds  # noqa: E402


PYPI_PACKAGE_URL = "https://pypi.org/pypi/{package}/json"
STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
DEPENDENCY_LINE_RE = re.compile(
    r'^(?P<indent>\s*)"playwright(?P<specifier>[^"]*)",(?P<suffix>.*)$'
)


def stable_version_tuple(version: str) -> tuple[int, int, int]:
    match = STABLE_VERSION_RE.match(version)
    if not match:
        raise ValueError(
            f"Expected a stable x.y.z Playwright version, got {version!r}."
        )
    return tuple(int(part) for part in match.groups())


def has_installable_file(files: list[dict[str, Any]]) -> bool:
    return any(not file.get("yanked", False) for file in files)


def fetch_latest_stable_version(package: str, *, timeout: float) -> str:
    quoted_package = urllib.parse.quote(package, safe="")
    request = urllib.request.Request(
        PYPI_PACKAGE_URL.format(package=quoted_package),
        headers={"User-Agent": "rotunda-ci"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        metadata = json.loads(response.read().decode("utf-8"))

    candidates: list[tuple[tuple[int, int, int], str]] = []
    for version, files in metadata["releases"].items():
        match = STABLE_VERSION_RE.match(version)
        if not match or not has_installable_file(files):
            continue
        candidates.append((tuple(int(part) for part in match.groups()), version))

    if not candidates:
        raise ValueError(f"No stable releases found for {package}.")
    return max(candidates, key=lambda candidate: candidate[0])[1]


def find_playwright_dependency_line(lines: list[str]) -> tuple[int, re.Match[str]]:
    """Return the exact Playwright dependency line to update in place.

    We intentionally avoid loading and rewriting TOML here: stdlib tomllib is
    read-only, and a TOML writer would add another script dependency while
    risking unrelated formatting churn. The package metadata keeps this as a
    simple PEP 621 dependency string, so a narrow [project].dependencies scan
    lets the updater change only the Playwright upper bound.
    """
    in_project = False
    in_dependencies = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            in_dependencies = False
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = False
            in_dependencies = False
            continue
        if not in_project:
            continue
        if stripped == "dependencies = [":
            in_dependencies = True
            continue
        if in_dependencies and stripped == "]":
            in_dependencies = False
            continue
        if not in_dependencies:
            continue

        match = DEPENDENCY_LINE_RE.match(line)
        if match:
            return index, match

    raise ValueError(
        "Could not find the Playwright dependency in [project].dependencies."
    )


def require_playwright_version_bounds(pyproject_path: Path) -> tuple[str, str]:
    minimum_version, maximum_version = read_playwright_version_bounds(pyproject_path)
    if not minimum_version:
        raise ValueError(
            "The Playwright dependency must include a >=x.y.z lower bound."
        )
    if not maximum_version:
        raise ValueError(
            "The Playwright dependency must include a <=x.y.z upper bound."
        )
    return minimum_version, maximum_version


def update_playwright_max(pyproject_path: Path, latest_version: str) -> tuple[str, str]:
    minimum_version, current_maximum = require_playwright_version_bounds(pyproject_path)
    lines = pyproject_path.read_text(encoding="utf-8").splitlines()
    index, match = find_playwright_dependency_line(lines)

    stable_version_tuple(latest_version)
    lines[index] = (
        f'{match.group("indent")}"playwright>={minimum_version},<={latest_version}",'
        f"{match.group('suffix')}"
    )
    pyproject_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return minimum_version, current_maximum


def write_github_output(path: Path | None, name: str, value: str) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


@click.command()
@click.option(
    "--package",
    "package_name",
    default="playwright",
    show_default=True,
    help="PyPI package name to inspect.",
)
@click.option(
    "--pyproject",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("pythonlib/pyproject.toml"),
    show_default=True,
    help="Path to the pyproject.toml containing the Playwright dependency.",
)
@click.option(
    "--timeout",
    default=20.0,
    show_default=True,
    type=float,
    help="PyPI request timeout in seconds.",
)
@click.option(
    "--latest-version",
    help="Override the latest version for deterministic local checks.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report whether an update is needed without editing files.",
)
@click.option(
    "--github-output",
    type=click.Path(path_type=Path),
    help="Optional path to GITHUB_OUTPUT.",
)
def main(
    package_name: str,
    pyproject: Path,
    timeout: float,
    latest_version: str | None,
    dry_run: bool,
    github_output: Path | None,
) -> None:
    """Bump Rotunda's validated Playwright upper bound."""
    latest_version = latest_version or fetch_latest_stable_version(
        package_name,
        timeout=timeout,
    )
    minimum_version, current_maximum = require_playwright_version_bounds(pyproject)

    latest_tuple = stable_version_tuple(latest_version)
    current_tuple = stable_version_tuple(current_maximum)
    update_needed = latest_tuple > current_tuple

    if update_needed and not dry_run:
        update_playwright_max(pyproject, latest_version)

    pr_title = f"autoplaywright: {latest_version}"
    branch_name = f"automation/autoplaywright-{latest_version}"
    outputs = {
        "minimum_version": minimum_version,
        "current_max_version": current_maximum,
        "latest_version": latest_version,
        "update_needed": "true" if update_needed else "false",
        "pr_title": pr_title,
        "branch_name": branch_name,
    }
    for name, value in outputs.items():
        write_github_output(github_output, name, value)

    print(f"Current Playwright max: {current_maximum}")
    print(f"Latest stable Playwright: {latest_version}")
    print(f"Update needed: {'yes' if update_needed else 'no'}")


if __name__ == "__main__":
    main()
