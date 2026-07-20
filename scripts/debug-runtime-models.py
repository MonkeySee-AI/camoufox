#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "click>=8.1",
# ]
# ///
"""Compile and run the native runtime model diagnostic harness."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click


ROOT = Path(__file__).resolve().parents[1]
BROWSERBUILD_DIR = ROOT / "browserbuild"
RUNTIME_DIR = BROWSERBUILD_DIR / "additions" / "rotundacfg"
FIXTURE_DIR = ROOT / "__tests__" / "fixtures" / "cpp"
SOURCE = ROOT / "scripts" / "runtime-model-debug.cpp"
DEFAULT_OUTPUT_ROOT = ROOT / "Training" / "debug_runtime"
DEFAULT_MOUSE_MODEL = BROWSERBUILD_DIR / "bundle" / "runtime-models" / "mouse.safetensors"
DEFAULT_KEYBOARD_MODEL = BROWSERBUILD_DIR / "bundle" / "runtime-models" / "keyboard.safetensors"


@dataclass(frozen=True)
class DiagnosticOptions:
    mouse_model: Path
    keyboard_model: Path
    output: Path | None
    build_dir: Path
    binary: Path | None
    compiler: str | None
    mouse_max_steps: int
    mouse_click_threshold: float
    mouse_min_dt_ms: float
    mouse_path_curve_sigma: float
    mouse_random_seed: int
    keyboard_max_steps: int
    keyboard_structured_extra_steps: int
    keyboard_canonical_bias: float
    keyboard_learned_typo_threshold: float
    keyboard_max_typos: int
    keyboard_sample_typos: bool
    keyboard_timing_jitter_sigma: float
    keyboard_timing_temperature: float
    keyboard_action_temperature: float
    keyboard_pause_probability: float
    keyboard_pause_mean_ms: float
    keyboard_random_seed: int
    include_vectors: bool
    print_json: bool


def newer_than_any(path: Path, inputs: list[Path]) -> bool:
    if not path.exists():
        return False
    binary_mtime = path.stat().st_mtime
    return all(binary_mtime >= item.stat().st_mtime for item in inputs if item.exists())


def compile_binary(args: DiagnosticOptions) -> Path:
    compiler = args.compiler or shutil.which("clang++") or shutil.which("c++")
    if compiler is None:
        raise SystemExit("No C++ compiler found. Install clang++ or pass --compiler.")

    build_dir = args.build_dir.expanduser().resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    binary = args.binary.expanduser().resolve() if args.binary else build_dir / "runtime_model_debug"
    inputs = [
        SOURCE,
        RUNTIME_DIR / "RuntimeWeights.cpp",
        RUNTIME_DIR / "MouseRuntime.cpp",
        RUNTIME_DIR / "KeyboardRuntime.cpp",
        RUNTIME_DIR / "RuntimeWeights.hpp",
        RUNTIME_DIR / "MouseRuntime.hpp",
        RUNTIME_DIR / "KeyboardRuntime.hpp",
    ]
    if newer_than_any(binary, inputs):
        return binary

    command = [
        compiler,
        "-std=c++17",
        "-O0",
        "-g",
        "-I",
        str(FIXTURE_DIR),
        "-I",
        str(RUNTIME_DIR),
        str(RUNTIME_DIR / "RuntimeWeights.cpp"),
        str(RUNTIME_DIR / "MouseRuntime.cpp"),
        str(RUNTIME_DIR / "KeyboardRuntime.cpp"),
        str(SOURCE),
        "-o",
        str(binary),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    return binary


def run_diagnostics(args: DiagnosticOptions, binary: Path) -> dict[str, Any]:
    mouse_model = args.mouse_model.expanduser().resolve()
    keyboard_model = args.keyboard_model.expanduser().resolve()
    for model in [mouse_model, keyboard_model]:
        if not model.is_file():
            raise SystemExit(f"Model file does not exist: {model}")

    command = [
        str(binary),
        "--mouse-model",
        str(mouse_model),
        "--keyboard-model",
        str(keyboard_model),
        "--mouse-max-steps",
        str(args.mouse_max_steps),
        "--mouse-click-threshold",
        str(args.mouse_click_threshold),
        "--mouse-min-dt-ms",
        str(args.mouse_min_dt_ms),
        "--mouse-path-curve-sigma",
        str(args.mouse_path_curve_sigma),
        "--mouse-random-seed",
        str(args.mouse_random_seed),
        "--keyboard-max-steps",
        str(args.keyboard_max_steps),
        "--keyboard-structured-extra-steps",
        str(args.keyboard_structured_extra_steps),
        "--keyboard-canonical-bias",
        str(args.keyboard_canonical_bias),
        "--keyboard-learned-typo-threshold",
        str(args.keyboard_learned_typo_threshold),
        "--keyboard-max-typos",
        str(args.keyboard_max_typos),
        "--keyboard-timing-jitter-sigma",
        str(args.keyboard_timing_jitter_sigma),
        "--keyboard-timing-temperature",
        str(args.keyboard_timing_temperature),
        "--keyboard-action-temperature",
        str(args.keyboard_action_temperature),
        "--keyboard-pause-probability",
        str(args.keyboard_pause_probability),
        "--keyboard-pause-mean-ms",
        str(args.keyboard_pause_mean_ms),
        "--keyboard-random-seed",
        str(args.keyboard_random_seed),
    ]
    if args.keyboard_sample_typos:
        command.append("--keyboard-sample-typos")
    if args.include_vectors:
        command.append("--include-vectors")

    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def write_report(args: DiagnosticOptions, report: dict[str, Any]) -> Path:
    if args.output:
        output = args.output.expanduser().resolve()
    else:
        output = (
            args.build_dir.expanduser().resolve()
            / f"runtime-model-debug-{time.strftime('%Y%m%d-%H%M%S')}.json"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return output


def fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}f}"
    return str(value)


def print_summary(report: dict[str, Any], report_path: Path) -> None:
    print(f"Wrote {report_path}")
    print("\nMouse cases:")
    for case in report["mouseCases"]:
        summary = case["summary"]
        print(
            "  "
            f"{case['name']}: steps={summary['pointCount']} "
            f"total={fmt(summary['totalMs'], 1)}ms "
            f"speed={fmt(summary['averageSpeedPxPerSec'], 1)}px/s "
            f"straightness={fmt(summary['straightnessRatio'], 3)} "
            f"max_perp={fmt(summary['maxAbsPerpPx'], 1)}px "
            f"dt_cv={fmt(summary['dtMs']['cv'], 3)} "
            f"zero_dt={summary['zeroDtCount']} "
            f"linear={summary['linearPathFlag']} "
            f"slow={summary['slowPathFlag']}"
        )

    print("\nKeyboard cases:")
    for case in report["keyboardCases"]:
        summary = case["summary"]
        print(
            "  "
            f"{case['name']}: rows={summary['rowCount']} "
            f"total={fmt(summary['totalMs'], 1)}ms "
            f"dt_median={fmt(summary['dtMs']['median'], 1)}ms "
            f"dt_cv={fmt(summary['dtMs']['cv'], 3)} "
            f"space_ratio={fmt(summary['spaceToNonSpaceMeanDtRatio'], 3)} "
            f"after_space_ratio={fmt(summary['afterSpaceToAfterNonSpaceMeanDtRatio'], 3)} "
            f"typos={summary['learnedTypoCount']} "
            f"repairs={summary['repairCount']} "
            f"zero_dt={summary['zeroDtCount']} "
            f"unique_dt={summary['uniqueRoundedDtMsCount']} "
            f"after_space_pause={summary['afterSpacePauseFlag']} "
            f"decode_failed={case['decodeFailed']}"
        )


@click.command()
@click.option("--mouse-model", type=click.Path(path_type=Path), default=DEFAULT_MOUSE_MODEL)
@click.option("--keyboard-model", type=click.Path(path_type=Path), default=DEFAULT_KEYBOARD_MODEL)
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--build-dir", type=click.Path(path_type=Path), default=DEFAULT_OUTPUT_ROOT)
@click.option("--binary", type=click.Path(path_type=Path), default=None)
@click.option("--compiler", default=None)
@click.option("--mouse-max-steps", type=int, default=128, show_default=True)
@click.option("--mouse-click-threshold", type=float, default=0.98, show_default=True)
@click.option("--mouse-min-dt-ms", type=float, default=4.0, show_default=True)
@click.option("--mouse-path-curve-sigma", type=float, default=0.04, show_default=True)
@click.option("--mouse-random-seed", type=int, default=13, show_default=True)
@click.option("--keyboard-max-steps", type=int, default=256, show_default=True)
@click.option("--keyboard-structured-extra-steps", type=int, default=-1, show_default=True)
@click.option("--keyboard-canonical-bias", type=float, default=1.5, show_default=True)
@click.option("--keyboard-learned-typo-threshold", type=float, default=0.05, show_default=True)
@click.option("--keyboard-max-typos", type=int, default=-1, show_default=True)
@click.option("--keyboard-sample-typos/--no-keyboard-sample-typos", default=True, show_default=True)
@click.option("--keyboard-timing-jitter-sigma", type=float, default=0.0, show_default=True)
@click.option("--keyboard-timing-temperature", type=float, default=0.25, show_default=True)
@click.option("--keyboard-action-temperature", type=float, default=0.6, show_default=True)
@click.option("--keyboard-pause-probability", type=float, default=0.0, show_default=True)
@click.option("--keyboard-pause-mean-ms", type=float, default=35.0, show_default=True)
@click.option("--keyboard-random-seed", type=int, default=13, show_default=True)
@click.option(
    "--include-vectors",
    is_flag=True,
    help="Include hidden/input vectors in addition to per-step heads and top logits.",
)
@click.option(
    "--print-json",
    is_flag=True,
    help="Print the full JSON report to stdout after writing it.",
)
def main(**kwargs: Any) -> None:
    """Run C++ runtime-model diagnostics against safetensor bundles."""
    args = DiagnosticOptions(**kwargs)
    binary = compile_binary(args)
    report = run_diagnostics(args, binary)
    report_path = write_report(args, report)
    print_summary(report, report_path)
    if args.print_json:
        json.dump(report, sys.stdout, indent=2)
        print()


if __name__ == "__main__":
    main()
