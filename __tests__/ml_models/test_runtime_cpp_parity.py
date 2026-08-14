from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
import torch
from rotunda_models.constants import (
    CHAR_EOS,
    CHAR_PAD,
    CHAR_SEP,
    CHAR_UNK,
    KEY_BACKSPACE,
    KEY_STOP,
    KEY_UNKNOWN_ACTION,
    MOUSE_ACTIONS,
)
from rotunda_models.generation import decode_keyboard_rows, simulate_mouse_click_rows
from rotunda_models.models.keyboard import KeyboardActionGRU
from rotunda_models.models.mouse import MouseTrajectoryGRU
from rotunda_models.runtime_export import export_runtime_checkpoint
from rotunda_models.types import MouseEpisode

APPROX = {"rel": 1e-4, "abs": 1e-5}
KEYBOARD_DETERMINISTIC_DECODE = {
    "sample": False,
    "temperature": 0.0,
    "sample_typos": False,
    "timing_jitter_sigma": 0.0,
    "pause_probability": 0.0,
    "pause_mean_ms": 0.0,
    "random_seed": 0,
    "timing_temperature": 0.0,
    "action_temperature": 0.0,
}


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def runtime_probe(repo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = shutil.which("clang++") or shutil.which("c++")
    if compiler is None:
        pytest.skip("No C++ compiler is available for runtime parity probe.")

    # Compile the moved browser runtime sources into the probe used by the
    # end-to-end native/Python parity checks below.
    binary = tmp_path_factory.mktemp("runtime-probe") / "runtime_probe"
    runtime_dir = repo_root / "browserbuild" / "additions" / "rotundacfg"
    fixture_dir = repo_root / "__tests__" / "fixtures" / "cpp"
    command = [
        compiler,
        "-std=c++17",
        "-O0",
        "-I",
        str(fixture_dir),
        "-I",
        str(runtime_dir),
        str(runtime_dir / "RuntimeWeights.cpp"),
        str(runtime_dir / "MouseRuntime.cpp"),
        str(runtime_dir / "KeyboardRuntime.cpp"),
        str(fixture_dir / "runtime_probe.cpp"),
        "-o",
        str(binary),
    ]
    subprocess.run(command, cwd=repo_root, check=True, text=True, capture_output=True)
    return binary


def _copy_deterministic_weights(model: torch.nn.Module, *, seed_offset: float = 0.0) -> None:
    with torch.no_grad():
        for index, parameter in enumerate(model.parameters()):
            values = torch.linspace(
                -0.07 + seed_offset,
                0.09 + seed_offset,
                parameter.numel(),
                dtype=parameter.dtype,
            ).reshape(parameter.shape)
            parameter.copy_(values + (index * 0.003))


def _export_mouse_checkpoint(tmp_path: Path) -> tuple[Path, MouseTrajectoryGRU]:
    model_config = {
        "condition_dim": 7,
        "previous_dim": 3 + len(MOUSE_ACTIONS) + 1,
        "hidden_size": 6,
        "action_count": len(MOUSE_ACTIONS),
        "layers": 1,
        "dropout": 0.0,
    }
    model = MouseTrajectoryGRU(**model_config)
    _copy_deterministic_weights(model)
    with torch.no_grad():
        model.pos_head.weight.zero_()
        model.pos_head.bias.copy_(torch.tensor([0.24, 0.015]))
        model.action_head.weight.zero_()
        model.action_head.bias.copy_(torch.tensor([4.0, -4.0, -4.0, -4.0, -4.0]))
    model.eval()

    checkpoint_path = tmp_path / "mouse.pt"
    runtime_path = tmp_path / "mouse.safetensors"
    torch.save(
        {
            "kind": "mouse_click_gru",
            "model_config": model_config,
            "actions": MOUSE_ACTIONS,
            "coordinate_scale": 100.0,
            "position_frame": "goal_relative_delta",
            "model_state": model.state_dict(),
        },
        checkpoint_path,
    )
    export_runtime_checkpoint(checkpoint_path, runtime_path)
    return runtime_path, model


def _write_keyboard_runtime_checkpoint(
    tmp_path: Path,
    name: str,
    model: KeyboardActionGRU,
    model_config: dict,
    char_to_id: dict[str, int],
    action_to_id: dict[str, int],
    *,
    sequence_mode: str = "raw",
) -> tuple[Path, KeyboardActionGRU, dict]:
    id_to_action = {index: action for action, index in action_to_id.items()}
    checkpoint = {
        "kind": "keyboard_action_gru",
        "model_config": model_config,
        "char_to_id": char_to_id,
        "action_to_id": action_to_id,
        "id_to_action": id_to_action,
        "sequence_mode": sequence_mode,
        "model_state": model.state_dict(),
    }
    checkpoint_path = tmp_path / f"{name}.pt"
    runtime_path = tmp_path / f"{name}.safetensors"
    torch.save(checkpoint, checkpoint_path)
    export_runtime_checkpoint(checkpoint_path, runtime_path)
    return runtime_path, model, checkpoint


def test_runtime_weights_resolves_bundled_model_next_to_executable(runtime_probe: Path) -> None:
    model_dir = runtime_probe.parent / "runtime-models"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "mouse-test.safetensors"
    model_path.write_bytes(b"not a real model")

    result = subprocess.run(
        [runtime_probe, "resolve-model", model_path.name],
        check=True,
        text=True,
        capture_output=True,
    )

    assert Path(result.stdout.strip()).resolve() == model_path.resolve()


def test_runtime_weights_resolves_bundled_model_from_resources_layout(
    runtime_probe: Path,
) -> None:
    model_dir = runtime_probe.parent.parent / "Resources" / "runtime-models"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "keyboard-test.safetensors"
    model_path.write_bytes(b"not a real model")

    result = subprocess.run(
        [runtime_probe, "resolve-model", model_path.name],
        check=True,
        text=True,
        capture_output=True,
    )

    assert Path(result.stdout.strip()).resolve() == model_path.resolve()


def test_runtime_weights_resolves_bundled_model_from_nested_macos_helper(
    runtime_probe: Path,
    tmp_path: Path,
) -> None:
    helper_dir = (
        tmp_path
        / "Rotunda.app"
        / "Contents"
        / "MacOS"
        / "plugin-container.app"
        / "Contents"
        / "MacOS"
    )
    resources_dir = tmp_path / "Rotunda.app" / "Contents" / "Resources" / "runtime-models"
    helper_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)
    helper_probe = helper_dir / runtime_probe.name
    shutil.copy2(runtime_probe, helper_probe)
    model_path = resources_dir / "keyboard-test.safetensors"
    model_path.write_bytes(b"not a real model")

    result = subprocess.run(
        [helper_probe, "resolve-model", model_path.name],
        check=True,
        text=True,
        capture_output=True,
    )

    assert Path(result.stdout.strip()).resolve() == model_path.resolve()


def _export_keyboard_checkpoint(
    tmp_path: Path,
    *,
    timing_distribution: str = "point",
) -> tuple[Path, KeyboardActionGRU, dict]:
    char_to_id = {
        CHAR_PAD: 0,
        CHAR_UNK: 1,
        CHAR_EOS: 2,
        CHAR_SEP: 3,
        "a": 4,
        "b": 5,
    }
    action_to_id = {"a": 0, "b": 1, KEY_BACKSPACE: 2, KEY_STOP: 3}
    model_config = {
        "char_vocab_size": len(char_to_id),
        "action_vocab_size": len(action_to_id),
        "hidden_size": 5,
        "char_embed_size": 4,
        "action_embed_size": 3,
        "layers": 1,
        "dropout": 0.0,
        "learned_typo_head": True,
        "predict_press_count_head": True,
    }
    if timing_distribution != "point":
        model_config["timing_distribution"] = timing_distribution
    model = KeyboardActionGRU(**model_config)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.dt_head.bias[0] = math.log1p(12.0)
        model.action_head.bias[action_to_id["b"]] = 4.0
        model.action_head.bias[action_to_id["a"]] = 2.0
        model.action_head.bias[action_to_id[KEY_BACKSPACE]] = 1.0
        model.action_head.bias[action_to_id[KEY_STOP]] = -4.0
        model.typo_head.bias.fill_(-8.0)
        model.press_count_head.bias.fill_(math.log(1.0))
    model.eval()
    return _write_keyboard_runtime_checkpoint(
        tmp_path,
        f"keyboard-{timing_distribution}",
        model,
        model_config,
        char_to_id,
        action_to_id,
        sequence_mode="raw",
    )


def _export_keyboard_checkpoint_with_condition_only_chars(
    tmp_path: Path,
) -> tuple[Path, KeyboardActionGRU, dict]:
    char_to_id = {
        CHAR_PAD: 0,
        CHAR_UNK: 1,
        CHAR_EOS: 2,
        CHAR_SEP: 3,
        "@": 4,
        "C": 5,
        "a": 6,
        "t": 7,
    }
    action_to_id = {"a": 0, "t": 1, KEY_BACKSPACE: 2, KEY_STOP: 3}
    model_config = {
        "char_vocab_size": len(char_to_id),
        "action_vocab_size": len(action_to_id),
        "hidden_size": 5,
        "char_embed_size": 4,
        "action_embed_size": 3,
        "layers": 1,
        "dropout": 0.0,
        "learned_typo_head": True,
        "predict_press_count_head": True,
    }
    model = KeyboardActionGRU(**model_config)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.action_head.bias[action_to_id["a"]] = 3.0
        model.action_head.bias[action_to_id["t"]] = 2.0
        model.action_head.bias[action_to_id[KEY_STOP]] = -5.0
        model.typo_head.bias.fill_(-8.0)
        model.press_count_head.bias.fill_(math.log(2.0))
    model.eval()
    return _write_keyboard_runtime_checkpoint(
        tmp_path,
        "keyboard-condition-only-chars",
        model,
        model_config,
        char_to_id,
        action_to_id,
    )


def _export_learned_typo_keyboard_checkpoint(
    tmp_path: Path,
    *,
    predict_press_count_head: bool = False,
    predicted_press_count: float = 3.0,
) -> tuple[Path, KeyboardActionGRU, dict]:
    char_to_id = {
        CHAR_PAD: 0,
        CHAR_UNK: 1,
        CHAR_EOS: 2,
        CHAR_SEP: 3,
        "a": 4,
        "x": 5,
    }
    action_to_id = {"a": 0, "x": 1, KEY_BACKSPACE: 2, KEY_STOP: 3}
    model_config = {
        "char_vocab_size": len(char_to_id),
        "action_vocab_size": len(action_to_id),
        "hidden_size": 5,
        "char_embed_size": 4,
        "action_embed_size": 3,
        "layers": 1,
        "dropout": 0.0,
        "learned_typo_head": True,
        "predict_press_count_head": predict_press_count_head,
    }
    model = KeyboardActionGRU(**model_config)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.action_head.bias[action_to_id["a"]] = 4.0
        model.action_head.bias[action_to_id[KEY_BACKSPACE]] = 3.0
        model.action_head.bias[action_to_id[KEY_STOP]] = -4.0
        model.typo_head.bias.fill_(8.0)
        model.typo_action_head.bias[action_to_id["x"]] = 6.0
        if model.press_count_head is not None:
            model.press_count_head.bias.fill_(math.log(predicted_press_count))
    model.eval()
    return _write_keyboard_runtime_checkpoint(
        tmp_path,
        "keyboard-learned-typo",
        model,
        model_config,
        char_to_id,
        action_to_id,
    )


def _export_unknown_action_keyboard_checkpoint(
    tmp_path: Path,
) -> tuple[Path, KeyboardActionGRU, dict]:
    char_to_id = {
        CHAR_PAD: 0,
        CHAR_UNK: 1,
        CHAR_EOS: 2,
        CHAR_SEP: 3,
    }
    action_to_id = {KEY_UNKNOWN_ACTION: 0, KEY_BACKSPACE: 1, KEY_STOP: 2}
    model_config = {
        "char_vocab_size": len(char_to_id),
        "action_vocab_size": len(action_to_id),
        "hidden_size": 5,
        "char_embed_size": 4,
        "action_embed_size": 3,
        "layers": 1,
        "dropout": 0.0,
        "learned_typo_head": True,
        "predict_press_count_head": True,
    }
    model = KeyboardActionGRU(**model_config)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.action_head.bias[action_to_id[KEY_UNKNOWN_ACTION]] = 4.0
        model.action_head.bias[action_to_id[KEY_STOP]] = -4.0
        model.press_count_head.bias.fill_(math.log(1.0))
    model.eval()
    return _write_keyboard_runtime_checkpoint(
        tmp_path,
        "keyboard-unknown-action",
        model,
        model_config,
        char_to_id,
        action_to_id,
    )


def _run_probe(binary: Path, *args: object) -> dict:
    result = subprocess.run(
        [str(binary), *[str(arg) for arg in args]],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def _run_keyboard_probe(
    binary: Path,
    runtime_path: Path,
    *,
    initial: str,
    final: str,
    max_steps: int,
    decode_mode: str = "constrained",
    structured_extra_steps: int = 0,
    canonical_bias: float = 1.5,
    learned_typo_threshold: float = 0.05,
    max_typos: int = -1,
) -> dict:
    return _run_probe(
        binary,
        "keyboard",
        runtime_path,
        initial,
        final,
        max_steps,
        decode_mode,
        structured_extra_steps,
        canonical_bias,
        learned_typo_threshold,
        max_typos,
        int(KEYBOARD_DETERMINISTIC_DECODE["sample_typos"]),
        KEYBOARD_DETERMINISTIC_DECODE["timing_jitter_sigma"],
        KEYBOARD_DETERMINISTIC_DECODE["pause_probability"],
        KEYBOARD_DETERMINISTIC_DECODE["pause_mean_ms"],
        KEYBOARD_DETERMINISTIC_DECODE["random_seed"],
        KEYBOARD_DETERMINISTIC_DECODE["timing_temperature"],
        KEYBOARD_DETERMINISTIC_DECODE["action_temperature"],
    )


def _python_keyboard_rows(
    checkpoint: dict,
    model: KeyboardActionGRU,
    *,
    initial: str,
    final: str,
    max_steps: int,
    decode_mode: str = "constrained",
    structured_extra_steps: int = 0,
    canonical_bias: float = 1.5,
    learned_typo_threshold: float = 0.05,
    max_typos: int = -1,
) -> list[dict]:
    return decode_keyboard_rows(
        checkpoint=checkpoint,
        model=model,
        final_string=final,
        device=torch.device("cpu"),
        max_steps=max_steps,
        decode_mode=decode_mode,
        sample=bool(KEYBOARD_DETERMINISTIC_DECODE["sample"]),
        temperature=float(KEYBOARD_DETERMINISTIC_DECODE["temperature"]),
        initial_string=initial,
        structured_extra_steps=structured_extra_steps,
        canonical_bias=canonical_bias,
        max_typos=max_typos,
        typo_seed=None,
        learned_typo_threshold=learned_typo_threshold,
        timing_temperature=float(KEYBOARD_DETERMINISTIC_DECODE["timing_temperature"]),
        timing_seed=None,
    )


def _assert_keyboard_rows_match(cpp_rows: list[dict], py_rows: list[dict]) -> None:
    assert len(cpp_rows) == len(py_rows)
    for actual, expected in zip(cpp_rows, py_rows, strict=True):
        for key in ["offsetMs", "dtMs"]:
            assert actual[key] == pytest.approx(expected[key], **APPROX)
        for key in ["action", "textAfter", "stepKind"]:
            assert actual[key] == expected[key]


def test_mouse_runtime_e2e_matches_python_decoder(runtime_probe: Path, tmp_path: Path) -> None:
    runtime_path, model = _export_mouse_checkpoint(tmp_path)
    args = {
        "from_x": 12.5,
        "from_y": 18.0,
        "to_x": 88.25,
        "to_y": 63.5,
        "click_at_end": True,
        "max_steps": 7,
        "click_threshold": 0.72,
        "min_dt_ms": 4.0,
        "coordinate_scale": 100.0,
    }

    cpp = _run_probe(
        runtime_probe,
        "mouse",
        runtime_path,
        args["from_x"],
        args["from_y"],
        args["to_x"],
        args["to_y"],
        1,
        args["max_steps"],
        args["click_threshold"],
        args["min_dt_ms"],
    )
    py = simulate_mouse_click_rows(
        model=model,
        episode=MouseEpisode(
            source="parity",
            start_x=args["from_x"],
            start_y=args["from_y"],
            dst_x=args["to_x"],
            dst_y=args["to_y"],
            steps=(),
        ),
        coordinate_scale=args["coordinate_scale"],
        position_frame="goal_relative_delta",
        actions=MOUSE_ACTIONS,
        device=torch.device("cpu"),
        max_steps=args["max_steps"],
        click_threshold=args["click_threshold"],
        min_dt_ms=args["min_dt_ms"],
        endpoint_guidance=True,
        sample=False,
        temperature=0.0,
        timing_temperature=0.0,
        timing_seed=None,
        click_at_end=args["click_at_end"],
    )

    assert cpp["usedFallback"] is False
    assert len(cpp["plan"]) == len(py)
    for actual, expected in zip(cpp["plan"], py, strict=True):
        for key in ["x", "y", "dtMs"]:
            assert actual[key] == pytest.approx(expected[key], **APPROX)
        assert MOUSE_ACTIONS[actual["action"]] == expected["action"]


@pytest.mark.parametrize("timing_distribution", ["point", "lognormal"])
def test_keyboard_structured_e2e_matches_python_decoder(
    runtime_probe: Path,
    tmp_path: Path,
    timing_distribution: str,
) -> None:
    runtime_path, model, checkpoint = _export_keyboard_checkpoint(
        tmp_path,
        timing_distribution=timing_distribution,
    )

    cpp = _run_keyboard_probe(
        runtime_probe,
        runtime_path,
        initial="a",
        final="ab",
        max_steps=4,
        structured_extra_steps=0,
    )
    py = _python_keyboard_rows(
        checkpoint,
        model,
        initial="a",
        final="ab",
        max_steps=4,
        structured_extra_steps=0,
    )

    assert cpp["usedPredictedPressCount"] is True
    assert cpp["rows"][-1]["textAfter"] == "ab"
    assert [row["stepKind"] for row in cpp["rows"]] == ["model_target"]
    _assert_keyboard_rows_match(cpp["rows"], py)


def test_keyboard_cpp_runtime_uses_learned_typo_head(runtime_probe: Path, tmp_path: Path) -> None:
    runtime_path, model, checkpoint = _export_learned_typo_keyboard_checkpoint(
        tmp_path,
        predict_press_count_head=True,
        predicted_press_count=1.0,
    )

    cpp = _run_keyboard_probe(
        runtime_probe,
        runtime_path,
        initial="",
        final="a",
        max_steps=4,
        structured_extra_steps=2,
        learned_typo_threshold=0.5,
        max_typos=1,
    )
    py = _python_keyboard_rows(
        checkpoint,
        model,
        initial="",
        final="a",
        max_steps=4,
        structured_extra_steps=2,
        learned_typo_threshold=0.5,
        max_typos=1,
    )

    assert [row["action"] for row in cpp["rows"]] == ["x", KEY_BACKSPACE, "a"]
    assert cpp["usedPredictedPressCount"] is True
    assert cpp["effectiveMaxSteps"] == 3
    assert cpp["rows"][0]["stepKind"] == "learned_typo"
    assert cpp["rows"][-1]["textAfter"] == "a"
    assert cpp["steps"][0]["learnedTypoProbability"] > 0.99
    _assert_keyboard_rows_match(cpp["rows"], py)


def test_keyboard_cpp_runtime_uses_predicted_press_budget(
    runtime_probe: Path,
    tmp_path: Path,
) -> None:
    runtime_path, model, checkpoint = _export_learned_typo_keyboard_checkpoint(
        tmp_path,
        predict_press_count_head=True,
        predicted_press_count=3.0,
    )

    cpp = _run_keyboard_probe(
        runtime_probe,
        runtime_path,
        initial="",
        final="a",
        max_steps=4,
        structured_extra_steps=0,
        learned_typo_threshold=0.5,
        max_typos=1,
    )
    py = _python_keyboard_rows(
        checkpoint,
        model,
        initial="",
        final="a",
        max_steps=4,
        structured_extra_steps=0,
        learned_typo_threshold=0.5,
        max_typos=1,
    )

    assert cpp["usedPredictedPressCount"] is True
    assert cpp["predictedPressCount"] == pytest.approx(3.0, **APPROX)
    assert cpp["effectiveMaxSteps"] == 3
    assert [row["action"] for row in cpp["rows"]] == ["x", KEY_BACKSPACE, "a"]
    assert cpp["rows"][-1]["textAfter"] == "a"
    _assert_keyboard_rows_match(cpp["rows"], py)


def test_keyboard_cpp_runtime_only_requires_target_edit_actions(runtime_probe: Path, tmp_path: Path) -> None:
    runtime_path, model, checkpoint = _export_keyboard_checkpoint_with_condition_only_chars(tmp_path)

    cpp = _run_keyboard_probe(
        runtime_probe,
        runtime_path,
        initial="@C",
        final="@Cat",
        max_steps=2,
        structured_extra_steps=0,
        canonical_bias=3.0,
        max_typos=0,
    )
    py = _python_keyboard_rows(
        checkpoint,
        model,
        initial="@C",
        final="@Cat",
        max_steps=2,
        structured_extra_steps=0,
        canonical_bias=3.0,
        max_typos=0,
    )

    assert [row["action"] for row in cpp["rows"]] == ["a", "t"]
    assert cpp["rows"][-1]["textAfter"] == "@Cat"
    _assert_keyboard_rows_match(cpp["rows"], py)


def test_keyboard_cpp_runtime_materializes_unknown_action(runtime_probe: Path, tmp_path: Path) -> None:
    runtime_path, model, checkpoint = _export_unknown_action_keyboard_checkpoint(tmp_path)

    cpp = _run_keyboard_probe(
        runtime_probe,
        runtime_path,
        initial="",
        final="Ω",
        max_steps=2,
        structured_extra_steps=0,
        max_typos=0,
    )
    py = _python_keyboard_rows(
        checkpoint,
        model,
        initial="",
        final="Ω",
        max_steps=2,
        structured_extra_steps=0,
        max_typos=0,
    )

    assert [row["action"] for row in cpp["rows"]] == ["Ω"]
    assert cpp["rows"][-1]["textAfter"] == "Ω"
    _assert_keyboard_rows_match(cpp["rows"], py)
