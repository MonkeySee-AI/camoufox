from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
import torch
from click.testing import CliRunner
from rotunda_models.cli.runtime import export_runtime_command
from rotunda_models.constants import KEY_UNKNOWN_ACTION, MOUSE_ACTIONS
from rotunda_models.models.keyboard import KeyboardActionGRU
from rotunda_models.models.mouse import MouseTrajectoryGRU
from rotunda_models.runtime_export import METADATA_KEY, export_runtime_checkpoint


def read_safetensors_header(path: Path) -> tuple[dict, dict]:
    data = path.read_bytes()
    header_len = struct.unpack("<Q", data[:8])[0]
    header = json.loads(data[8 : 8 + header_len].decode("utf-8"))
    metadata = json.loads(header["__metadata__"][METADATA_KEY])
    return header, metadata


def test_exports_mouse_checkpoint_as_safetensors(tmp_path: Path) -> None:
    model = MouseTrajectoryGRU(
        condition_dim=7,
        previous_dim=3 + len(MOUSE_ACTIONS) + 1,
        hidden_size=8,
        action_count=len(MOUSE_ACTIONS),
        layers=1,
        dropout=0.0,
    )
    checkpoint = {
        "kind": "mouse_click_gru",
        "model_config": {
            "condition_dim": 7,
            "previous_dim": 3 + len(MOUSE_ACTIONS) + 1,
            "hidden_size": 8,
            "action_count": len(MOUSE_ACTIONS),
            "layers": 1,
            "dropout": 0.0,
        },
        "actions": MOUSE_ACTIONS,
        "coordinate_scale": 100.0,
        "position_frame": "goal_relative_delta",
        "model_state": model.state_dict(),
    }
    checkpoint_path = tmp_path / "model.pt"
    output_path = tmp_path / "mouse.safetensors"
    torch.save(checkpoint, checkpoint_path)

    result = export_runtime_checkpoint(checkpoint_path, output_path)

    header, metadata = read_safetensors_header(output_path)
    assert result["kind"] == "mouse_click_gru"
    assert metadata["format"] == "rotunda-runtime-v1"
    assert metadata["coordinateScale"] == 100.0
    assert "gru.weight_ih_l0" in header
    assert header["gru.weight_ih_l0"]["dtype"] == "F32"


def test_exports_keyboard_checkpoint_as_safetensors(tmp_path: Path) -> None:
    char_to_id = {"<PAD>": 0, "<UNK>": 1, "<EOS>": 2, "<SEP>": 3, "h": 4}
    action_to_id = {"h": 0, "<BACKSPACE>": 1, "<STOP>": 2}
    id_to_action = {index: action for action, index in action_to_id.items()}
    model_config = {
        "char_vocab_size": len(char_to_id),
        "action_vocab_size": len(action_to_id),
        "hidden_size": 4,
        "char_embed_size": 3,
        "action_embed_size": 2,
        "layers": 1,
        "dropout": 0.0,
    }
    model = KeyboardActionGRU(**model_config)
    checkpoint = {
        "kind": "keyboard_action_gru",
        "model_config": model_config,
        "char_to_id": char_to_id,
        "action_to_id": action_to_id,
        "id_to_action": id_to_action,
        "sequence_mode": "constrained",
        "model_state": model.state_dict(),
    }
    checkpoint_path = tmp_path / "model.pt"
    output_path = tmp_path / "keyboard.safetensors"
    torch.save(checkpoint, checkpoint_path)

    result = export_runtime_checkpoint(checkpoint_path, output_path)

    header, metadata = read_safetensors_header(output_path)
    assert result["kind"] == "keyboard_action_gru"
    assert metadata["charToId"]["h"] == 4
    assert metadata["idToAction"]["2"] == "<STOP>"
    assert metadata["unknownAction"] == KEY_UNKNOWN_ACTION
    assert metadata["learnedTypoHead"] is False
    assert metadata["decodeDefaults"]["canonicalBias"] == 1.5
    assert metadata["decodeDefaults"]["maxTypos"] == -1
    assert metadata["decodeDefaults"]["structuredExtraStepRate"] == 0.03
    assert "decoder.weight_ih_l0" in header


def test_export_runtime_command_final_writes_browser_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Build a real checkpoint so --final proves the exported artifact and
    # manifest land in the browser build payload.
    model_config = {
        "condition_dim": 7,
        "previous_dim": 3 + len(MOUSE_ACTIONS) + 1,
        "hidden_size": 4,
        "action_count": len(MOUSE_ACTIONS),
        "layers": 1,
        "dropout": 0.0,
    }
    model = MouseTrajectoryGRU(**model_config)
    checkpoint_path = tmp_path / "model.pt"
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
    repo_root = tmp_path / "repo"
    (repo_root / "browserbuild" / "bundle").mkdir(parents=True)
    (repo_root / "ml-models").mkdir()
    monkeypatch.chdir(repo_root)

    result = CliRunner().invoke(
        export_runtime_command,
        ["--mouse-checkpoint", str(checkpoint_path), "--final"],
    )

    assert result.exit_code == 0, result.output
    output_path = repo_root / "browserbuild" / "bundle" / "runtime-models" / "mouse.safetensors"
    manifest_path = repo_root / "browserbuild" / "bundle" / "runtime-models" / "runtime-models.json"
    assert output_path.is_file()
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        {
            "kind": "mouse_click_gru",
            "path": output_path.name,
            "tensorCount": len(model.state_dict()),
        }
    ]
