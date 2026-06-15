from __future__ import annotations

from pathlib import Path

_FIXTURES_DIR = Path(__file__).resolve().parent


def get_fixtures_path(name: str) -> Path:
    fixture_path = (_FIXTURES_DIR / name).resolve()
    if fixture_path != _FIXTURES_DIR and _FIXTURES_DIR not in fixture_path.parents:
        raise ValueError(f"Fixture path escapes fixture directory: {name}")
    if not fixture_path.exists():
        raise FileNotFoundError(f"Unknown test fixture: {name}")
    return fixture_path


__all__ = ["get_fixtures_path"]
