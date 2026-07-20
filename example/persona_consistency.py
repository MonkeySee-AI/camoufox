#!/usr/bin/env python3
"""
Generate a small set of persona files, then verify they reproduce the same
CreepJS fingerprint IDs across browser relaunches.

This script intentionally saves the full launch/context payloads to disk and
uses those saved persona files for both verification rounds. That keeps the
persona JSON files as the source of truth for browser identity over time.

Example:
    export ROTUNDA_EXECUTABLE_PATH="$PWD/rotunda-152.0.6-beta.25/obj-aarch64-apple-darwin/dist/Rotunda.app/Contents/MacOS/rotunda"
    uv run --group dev python example/persona_consistency.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
from configparser import ConfigParser
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from rotunda.fingerprints import generate_context_fingerprint, generate_fingerprint
from rotunda.pkgman import launch_path
from rotunda.utils import get_env_vars, launch_options, validate_config

DEFAULT_PERSONA_COUNT = 5
DEFAULT_TIMEOUT_MS = 120_000
DEFAULT_URL = "https://abrahamjuliot.github.io/creepjs/"
ROTUNDA_CONFIG_PATH = "ROTUNDA_CONFIG_PATH"
FINGERPRINT_ID_PATTERN = re.compile(r"FP ID:\s*([a-f0-9]{16,})\b", re.IGNORECASE)


def main() -> int:
    args = parse_args()

    executable_path = resolve_executable_path(args.executable_path)
    ff_version = resolve_firefox_major(executable_path)
    persona_dir = args.persona_dir.resolve()
    persona_dir.mkdir(parents=True, exist_ok=True)

    print(f"Using executable: {executable_path}")
    print(f"Saving persona files to: {persona_dir}")
    print(f"Generating {args.count} personas.")

    persona_paths = create_persona_files(
        count=args.count,
        persona_dir=persona_dir,
        executable_path=executable_path,
        ff_version=ff_version,
        headless=not args.headful,
    )

    initial_results = run_round(
        label="initial",
        playwright_factory=sync_playwright,
        persona_paths=persona_paths,
        executable_path=executable_path,
        url=args.url,
        timeout_ms=args.timeout_ms,
        headless=not args.headful,
    )
    repeated_results = run_round(
        label="repeat",
        playwright_factory=sync_playwright,
        persona_paths=persona_paths,
        executable_path=executable_path,
        url=args.url,
        timeout_ms=args.timeout_ms,
        headless=not args.headful,
    )

    mismatches = [
        name
        for name in sorted(initial_results)
        if initial_results.get(name) != repeated_results.get(name)
    ]

    if mismatches:
        print("\nFingerprint drift detected:")
        for name in mismatches:
            print(f"  {name}: {initial_results[name]} -> {repeated_results[name]}")
        return 1

    print("\nAll personas were stable across relaunches.")
    for name in sorted(initial_results):
        print(f"  {name}: {initial_results[name]}")
    return 0


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Generate persona files and verify stable CreepJS FP IDs across relaunches."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_PERSONA_COUNT,
        help=f"number of personas to generate (default: {DEFAULT_PERSONA_COUNT})",
    )
    parser.add_argument(
        "--persona-dir",
        type=Path,
        default=repo_root / ".tmp" / "personas",
        help="directory used to write persona JSON files",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"page used to verify the fingerprint (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help=f"Playwright timeout in milliseconds (default: {DEFAULT_TIMEOUT_MS})",
    )
    parser.add_argument(
        "--executable-path",
        type=Path,
        default=None,
        help="path to a Rotunda executable; defaults to ROTUNDA_EXECUTABLE_PATH or the active install",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="show the browser windows during verification",
    )
    args = parser.parse_args()
    if args.count <= 0:
        parser.error("--count must be greater than zero")
    return args


def resolve_executable_path(explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        path = explicit_path.expanduser().resolve()
    else:
        env_path = os.environ.get("ROTUNDA_EXECUTABLE_PATH")
        if env_path:
            path = Path(env_path).expanduser().resolve()
        else:
            path = Path(launch_path()).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Rotunda executable not found: {path}")
    return path


def resolve_firefox_major(executable_path: Path) -> str:
    application_ini = executable_path.parent.parent / "Resources" / "application.ini"
    if not application_ini.exists():
        raise FileNotFoundError(f"application.ini not found next to executable: {application_ini}")

    parser = ConfigParser()
    parser.read(application_ini, encoding="utf-8")
    version = parser.get("App", "Version", fallback=None)
    if not version:
        raise RuntimeError(f"Unable to read App.Version from {application_ini}")
    return version.split(".", 1)[0]


def create_persona_files(
    *,
    count: int,
    persona_dir: Path,
    executable_path: Path,
    ff_version: str,
    headless: bool,
) -> List[Path]:
    persona_paths: List[Path] = []

    for index in range(1, count + 1):
        name = f"persona_{index:02d}"
        fingerprint = generate_fingerprint()
        launch_payload = launch_options(
            fingerprint=fingerprint,
            executable_path=executable_path,
            headless=headless,
            i_know_what_im_doing=True,
        )
        context_payload = generate_context_fingerprint(
            fingerprint=fingerprint,
            ff_version=ff_version,
        )

        persona = {
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ff_version": ff_version,
            "fingerprint": serialize_fingerprint(fingerprint),
            "launch_args": list(launch_payload.get("args", [])),
            "launch_config": decode_rotunda_config(launch_payload["env"]),
            "firefox_user_prefs": dict(launch_payload.get("firefox_user_prefs", {})),
            "context_config": serialize_profile(context_payload["config"]),
            "context_options": dict(context_payload["context_options"]),
            "init_script": context_payload["init_script"],
        }

        persona_path = persona_dir / f"{name}.json"
        persona_path.write_text(json.dumps(persona, indent=2, sort_keys=True), encoding="utf-8")
        persona_paths.append(persona_path)
        print(f"  wrote {persona_path.name}")

    return persona_paths


def serialize_fingerprint(fingerprint: Any) -> Dict[str, Any]:
    if is_dataclass(fingerprint):
        return asdict(fingerprint)
    model_dump = getattr(fingerprint, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    return dict(fingerprint)


def serialize_profile(profile: Any) -> Dict[str, Any]:
    model_dump = getattr(profile, "model_dump", None)
    if callable(model_dump):
        return model_dump(by_alias=True, exclude_none=True, mode="json")
    return dict(profile)


def run_round(
    *,
    label: str,
    playwright_factory: Any,
    persona_paths: Sequence[Path],
    executable_path: Path,
    url: str,
    timeout_ms: int,
    headless: bool,
) -> Dict[str, str]:
    print(f"\nRound: {label}")
    results: Dict[str, str] = {}

    with playwright_factory() as playwright:
        for persona_path in persona_paths:
            persona = load_persona(persona_path)
            fingerprint_id = run_persona(
                playwright=playwright,
                persona=persona,
                executable_path=executable_path,
                url=url,
                timeout_ms=timeout_ms,
                headless=headless,
            )
            results[persona["name"]] = fingerprint_id
            print(f"  {persona['name']}: {fingerprint_id}")

    return results


def load_persona(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_persona(
    *,
    playwright: Playwright,
    persona: Dict[str, Any],
    executable_path: Path,
    url: str,
    timeout_ms: int,
    headless: bool,
) -> str:
    browser = launch_browser_from_persona(
        playwright=playwright,
        persona=persona,
        executable_path=executable_path,
        headless=headless,
    )
    context: BrowserContext | None = None
    try:
        context = browser.new_context(**persona["context_options"])
        context.add_init_script(persona["init_script"])
        page = context.new_page()
        return extract_creepjs_id(page=page, url=url, timeout_ms=timeout_ms)
    finally:
        if context is not None:
            context.close()
        browser.close()


def launch_browser_from_persona(
    *,
    playwright: Playwright,
    persona: Dict[str, Any],
    executable_path: Path,
    headless: bool,
) -> Browser:
    launch_config = dict(persona["launch_config"])
    validate_config(launch_config, path=executable_path)

    browser = playwright.firefox.launch(
        args=list(persona.get("launch_args", [])),
        env={**get_env_vars(launch_config, user_agent_os(launch_config)), **os.environ},
        executable_path=str(executable_path),
        firefox_user_prefs=dict(persona.get("firefox_user_prefs", {})),
        headless=headless,
    )
    return browser


def extract_creepjs_id(*, page: Page, url: str, timeout_ms: int) -> str:
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_function(
        """
        () => {
          const pattern = /FP ID:\\s*[a-f0-9]{16,}\\b/i;
          return Array.from(document.querySelectorAll('.fingerprint-header'))
            .some(node => pattern.test(node.textContent || ''));
        }
        """,
        timeout=timeout_ms,
    )

    for text in page.locator(".fingerprint-header").all_inner_texts():
        fingerprint_id = parse_fp_id(text)
        if fingerprint_id:
            return fingerprint_id

    fingerprint_id = parse_fp_id(page.locator("body").inner_text())
    if fingerprint_id:
        return fingerprint_id

    raise RuntimeError("Failed to locate a CreepJS 'FP ID' value on the page.")


def parse_fp_id(text: str) -> str | None:
    match = FINGERPRINT_ID_PATTERN.search(text)
    if match:
        return match.group(1)
    return None


def decode_rotunda_config(env: Dict[str, Any]) -> Dict[str, Any]:
    with open(env[ROTUNDA_CONFIG_PATH], encoding="utf-8") as handle:
        return json.load(handle)


def user_agent_os(config: Dict[str, Any]) -> str:
    navigator = config.get("navigator", {})
    user_agent = navigator.get("userAgent", "") if isinstance(navigator, dict) else ""
    if "Windows" in user_agent:
        return "win"
    if "Macintosh" in user_agent:
        return "mac"
    return "lin"


if __name__ == "__main__":
    raise SystemExit(main())
