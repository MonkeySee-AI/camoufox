#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "click>=8.1",
# ]
# ///

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import click


PYPI_PACKAGE_URL = "https://pypi.org/pypi/{package}/json"
STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
DEFAULT_MINIMUM_VERSION = "1.51.0"
DEPENDENCY_LINE_RE = re.compile(r'^\s*"playwright(?P<specifier>[^"]*)",')
MINIMUM_SPECIFIER_RE = re.compile(r">=\s*(\d+\.\d+\.\d+)")
MAXIMUM_SPECIFIER_RE = re.compile(r"<=\s*(\d+\.\d+\.\d+)")


def _stable_version_tuple(version: str) -> tuple[int, int, int] | None:
    match = STABLE_VERSION_RE.match(version)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _has_installable_file(files: list[dict[str, Any]]) -> bool:
    return any(not file.get("yanked", False) for file in files)


def select_recent_minor_versions(
    releases: dict[str, list[dict[str, Any]]],
    *,
    limit: int,
    minimum_version: str,
    maximum_version: str | None = None,
) -> list[str]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    minimum = _stable_version_tuple(minimum_version)
    if minimum is None:
        raise ValueError(
            f"minimum_version must be a stable x.y.z version: {minimum_version}"
        )
    maximum = None
    if maximum_version:
        maximum = _stable_version_tuple(maximum_version)
        if maximum is None:
            raise ValueError(
                f"maximum_version must be a stable x.y.z version: {maximum_version}"
            )
        if maximum < minimum:
            raise ValueError(
                f"maximum_version must be greater than or equal to minimum_version: "
                f"{maximum_version} < {minimum_version}"
            )

    stable_versions: list[tuple[tuple[int, int, int], str]] = []
    for version, files in releases.items():
        parsed = _stable_version_tuple(version)
        if (
            parsed is None
            or parsed < minimum
            or (maximum is not None and parsed > maximum)
            or not _has_installable_file(files)
        ):
            continue
        stable_versions.append((parsed, version))

    latest_by_minor: dict[tuple[int, int], tuple[tuple[int, int, int], str]] = {}
    for parsed, version in sorted(stable_versions, key=lambda item: item[0]):
        latest_by_minor[(parsed[0], parsed[1])] = (parsed, version)

    recent = sorted(latest_by_minor.values(), key=lambda item: item[0])[-limit:]
    return [version for _, version in recent]


def fetch_pypi_metadata(package: str, *, timeout: float) -> dict[str, Any]:
    quoted_package = urllib.parse.quote(package, safe="")
    request = urllib.request.Request(
        PYPI_PACKAGE_URL.format(package=quoted_package),
        headers={"User-Agent": "rotunda-ci"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def read_playwright_version_bounds(
    pyproject_path: Path,
) -> tuple[str | None, str | None]:
    lines = pyproject_path.read_text(encoding="utf-8").splitlines()
    in_project = False
    in_dependencies = False

    for line in lines:
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
        if not match:
            continue
        specifier = match.group("specifier")
        minimum_match = MINIMUM_SPECIFIER_RE.search(specifier)
        maximum_match = MAXIMUM_SPECIFIER_RE.search(specifier)
        return (
            minimum_match.group(1) if minimum_match else None,
            maximum_match.group(1) if maximum_match else None,
        )

    raise ValueError(f"Could not find a Playwright dependency in {pyproject_path}.")


def write_github_output(path: Path, name: str, value: str) -> None:
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
    "--limit",
    default=10,
    show_default=True,
    type=click.IntRange(min=1),
    help="Number of recent major.minor release lines to return.",
)
@click.option(
    "--timeout",
    default=20.0,
    show_default=True,
    type=float,
    help="PyPI request timeout in seconds.",
)
@click.option(
    "--minimum-version",
    default=DEFAULT_MINIMUM_VERSION,
    show_default=True,
    help="Oldest stable Playwright version Rotunda supports.",
)
@click.option(
    "--maximum-version",
    help="Newest stable Playwright version Rotunda supports.",
)
@click.option(
    "--dependency-file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Read Playwright minimum and maximum bounds from this pyproject.toml file.",
)
@click.option(
    "--github-output",
    type=click.Path(path_type=Path),
    help="Optional path to GITHUB_OUTPUT.",
)
@click.option(
    "--output-name",
    default="versions",
    show_default=True,
    help="GITHUB_OUTPUT key to write.",
)
def main(
    package_name: str,
    limit: int,
    timeout: float,
    minimum_version: str,
    maximum_version: str | None,
    dependency_file: Path | None,
    github_output: Path | None,
    output_name: str,
) -> None:
    """Resolve recent stable Playwright release lines from PyPI."""
    if dependency_file:
        try:
            dependency_minimum, dependency_maximum = read_playwright_version_bounds(
                dependency_file
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        minimum_version = dependency_minimum or minimum_version
        maximum_version = dependency_maximum or maximum_version

    metadata = fetch_pypi_metadata(package_name, timeout=timeout)
    try:
        versions = select_recent_minor_versions(
            metadata["releases"],
            limit=limit,
            minimum_version=minimum_version,
            maximum_version=maximum_version,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if not versions:
        raise click.ClickException(f"No stable releases found for {package_name}.")

    output = json.dumps(versions, separators=(",", ":"))
    click.echo(output)

    if github_output:
        write_github_output(github_output, output_name, output)


if __name__ == "__main__":
    main()
