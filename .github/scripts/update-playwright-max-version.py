#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PYPI_PACKAGE_URL = "https://pypi.org/pypi/{package}/json"
STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
DEPENDENCY_LINE_RE = re.compile(
    r'^(?P<indent>\s*)"playwright(?P<specifier>[^"]*)",(?P<suffix>.*)$'
)
MINIMUM_SPECIFIER_RE = re.compile(r">=\s*(\d+\.\d+\.\d+)")
MAXIMUM_SPECIFIER_RE = re.compile(r"<=\s*(\d+\.\d+\.\d+)")


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


def read_playwright_bounds(pyproject_path: Path) -> tuple[str, str]:
    lines = pyproject_path.read_text(encoding="utf-8").splitlines()
    _, match = find_playwright_dependency_line(lines)
    specifier = match.group("specifier")
    minimum_match = MINIMUM_SPECIFIER_RE.search(specifier)
    maximum_match = MAXIMUM_SPECIFIER_RE.search(specifier)
    if not minimum_match:
        raise ValueError(
            "The Playwright dependency must include a >=x.y.z lower bound."
        )
    if not maximum_match:
        raise ValueError(
            "The Playwright dependency must include a <=x.y.z upper bound."
        )
    return minimum_match.group(1), maximum_match.group(1)


def update_playwright_max(pyproject_path: Path, latest_version: str) -> tuple[str, str]:
    lines = pyproject_path.read_text(encoding="utf-8").splitlines()
    index, match = find_playwright_dependency_line(lines)
    specifier = match.group("specifier")
    minimum_match = MINIMUM_SPECIFIER_RE.search(specifier)
    maximum_match = MAXIMUM_SPECIFIER_RE.search(specifier)
    if not minimum_match:
        raise ValueError(
            "The Playwright dependency must include a >=x.y.z lower bound."
        )
    if not maximum_match:
        raise ValueError(
            "The Playwright dependency must include a <=x.y.z upper bound."
        )

    minimum_version = minimum_match.group(1)
    current_maximum = maximum_match.group(1)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bump Rotunda's validated Playwright upper bound."
    )
    parser.add_argument(
        "--package",
        default="playwright",
        help="PyPI package name to inspect.",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pythonlib/pyproject.toml"),
        help="Path to the pyproject.toml containing the Playwright dependency.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="PyPI request timeout in seconds.",
    )
    parser.add_argument(
        "--latest-version",
        help="Override the latest version for deterministic local checks.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report whether an update is needed without editing files.",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        help="Optional path to GITHUB_OUTPUT.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    latest_version = args.latest_version or fetch_latest_stable_version(
        args.package,
        timeout=args.timeout,
    )
    minimum_version, current_maximum = read_playwright_bounds(args.pyproject)

    latest_tuple = stable_version_tuple(latest_version)
    current_tuple = stable_version_tuple(current_maximum)
    update_needed = latest_tuple > current_tuple

    if update_needed and not args.dry_run:
        update_playwright_max(args.pyproject, latest_version)

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
        write_github_output(args.github_output, name, value)

    print(f"Current Playwright max: {current_maximum}")
    print(f"Latest stable Playwright: {latest_version}")
    print(f"Update needed: {'yes' if update_needed else 'no'}")


if __name__ == "__main__":
    main()
