"""Click command for runtime model export."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..runtime_export import export_runtime_checkpoint
from .common import CONTEXT_SETTINGS, PATH_TYPE

FINAL_OUTPUT_DIR = Path("browserbuild") / "bundle" / "runtime-models"
MOUSE_OUTPUT = "mouse.safetensors"
KEYBOARD_OUTPUT = "keyboard.safetensors"
MANIFEST_OUTPUT = "runtime-models.json"


def _find_repo_root() -> Path:
    """Locate the repository root for release-bundle exports."""
    candidates = [Path.cwd(), *Path(__file__).resolve().parents]
    for candidate in candidates:
        if (candidate / "browserbuild" / "bundle").is_dir() and (candidate / "ml-models").is_dir():
            return candidate
    return Path.cwd()


def _resolve_output_dir(output_dir: Path | None, final: bool) -> Path:
    if final:
        if output_dir is not None:
            raise click.ClickException("--final writes to the browser bundle; omit --output-dir.")
        return _find_repo_root() / FINAL_OUTPUT_DIR
    if output_dir is None:
        raise click.ClickException("Specify --output-dir or --final.")
    return output_dir


def _manifest_artifact_path(path: str, output_dir: Path) -> str:
    """Return artifact paths relative to the manifest directory when possible."""
    artifact_path = Path(path)
    try:
        return str(artifact_path.relative_to(output_dir))
    except ValueError:
        try:
            return str(artifact_path.resolve().relative_to(output_dir.resolve()))
        except ValueError:
            return str(artifact_path)


@click.command(
    "export-runtime",
    context_settings=CONTEXT_SETTINGS,
    help="Export checkpoints for native runtime decoding.",
)
@click.option("--mouse-checkpoint", type=PATH_TYPE, default=None)
@click.option("--keyboard-checkpoint", type=PATH_TYPE, default=None)
@click.option("--output-dir", type=PATH_TYPE, default=None)
@click.option(
    "--final",
    "final",
    is_flag=True,
    help=f"Export to {FINAL_OUTPUT_DIR} for browser release packaging.",
)
@click.option("--mouse-output", default=MOUSE_OUTPUT, show_default=True)
@click.option("--keyboard-output", default=KEYBOARD_OUTPUT, show_default=True)
@click.option("--device", default=None)
def export_runtime_command(
    mouse_checkpoint,
    keyboard_checkpoint,
    output_dir,
    final: bool,
    mouse_output: str,
    keyboard_output: str,
    device: str | None,
) -> None:
    """Export selected checkpoints into native runtime artifacts."""
    outputs = []
    if mouse_checkpoint is None and keyboard_checkpoint is None:
        raise click.ClickException("Specify at least one of --mouse-checkpoint or --keyboard-checkpoint.")
    output_dir = _resolve_output_dir(output_dir, final)

    if mouse_checkpoint is not None:
        outputs.append(
            export_runtime_checkpoint(
                checkpoint_path=mouse_checkpoint,
                output_path=output_dir / mouse_output,
                device=device,
            )
        )
    if keyboard_checkpoint is not None:
        outputs.append(
            export_runtime_checkpoint(
                checkpoint_path=keyboard_checkpoint,
                output_path=output_dir / keyboard_output,
                device=device,
            )
        )

    manifest = {
        "format": "rotunda-runtime-manifest-v1",
        "artifacts": [
            {
                "kind": output["kind"],
                "path": _manifest_artifact_path(output["path"], output_dir),
                "tensorCount": output["tensorCount"],
            }
            for output in outputs
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / MANIFEST_OUTPUT
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    click.echo(json.dumps({"manifest": str(manifest_path), "artifacts": outputs}, indent=2, sort_keys=True))
