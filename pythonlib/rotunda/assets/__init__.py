from __future__ import annotations

from pathlib import Path

_ASSET_DIR = Path(__file__).resolve().parent
_ASSET_PATHS = {
    "connectRemoteJuggler.js": _ASSET_DIR / "connectRemoteJuggler.js",
    "launchServer.js": _ASSET_DIR / "launchServer.js",
    "playwrightUtilityEvalPatch.js": _ASSET_DIR / "playwrightUtilityEvalPatch.js",
    "repos.yml": _ASSET_DIR / "repos.yml",
    "territoryInfo.xml": _ASSET_DIR / "territoryInfo.xml",
    "warnings.yml": _ASSET_DIR / "warnings.yml",
}


def get_asset_by_name(name: str) -> Path:
    """
    Return the filesystem path for a packaged Rotunda asset.

    The asset name must be one of the known top-level package assets shipped in
    `rotunda.assets`.
    """
    try:
        asset_path = _ASSET_PATHS[name]
    except KeyError as error:
        available = ", ".join(sorted(_ASSET_PATHS))
        raise FileNotFoundError(f'Unknown Rotunda asset "{name}". Available: {available}') from error

    if not asset_path.is_file():
        raise FileNotFoundError(f"Rotunda asset is missing from the package: {asset_path}")
    return asset_path


__all__ = ["get_asset_by_name"]
