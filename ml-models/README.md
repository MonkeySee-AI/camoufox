# Rotunda Model Training

This package contains the raw pipeline to train models that simulate user actions: mouse movements and keyboard clicks.

## Install

The model code ships as its own `rotunda_models` Python package so PyTorch,
Pillow, and optional W&B dependencies stay out of the main `rotunda` package.

Run commands through `uv` without a manual install:

```bash
cd ml-models
uv run rotunda-models --help
```

From the repository root, select the workspace package:

```bash
uv run --package rotunda-models rotunda-models --help
```

The exposed console scripts are:

- `rotunda-models`
- `rotunda-models-sweep`
- `rotunda-models-render-debug-videos`

## What The Models Learn

Mouse clicks:

- Input: current pointer coordinate and destination click coordinate.
- Output: a variable-length sequence of `offsetMs`, `dtMs`, `x`, `y`, and action
  labels. The terminal action is a click.
- Training data: "motivated" click episodes that start after pointer rest and
  reach a click within two seconds.

Keyboard actions:

- Input: optional initial string plus final string.
- Output: a variable-length sequence of `offsetMs`, `dtMs`, and key actions.
- Training data: focused accessibility text snapshots. Training derives edit
  actions from every valid contiguous accessibility-field run, and treats
  leaving and later returning to the same field as a separate sequence.

The default data filter keeps events whose display size looks like a laptop
screen. Current configs use `1100-1920` by `700-1300` logical pixels and reject
events without a known screen size.

## Data Capture Contract

Recorder NDJSON rows are defined in `schemas/rotunda-ml-data-capture.openapi.yaml`.
The ML package consumes generated Pydantic models from
`rotunda_models/_generated_data_capture.py` rather than open-ended event dicts.

Regenerate the local models from the repository root after changing the schema:

```bash
bash scripts/generate-ml-data-models.sh
# or
make generate-ml-data-models
```

## Commands

Inspect available training data:

```bash
rotunda-models inspect recordings
```

Run a YAML-defined experiment from the repository root:

```bash
uv run --package rotunda-models rotunda-models train config/laptop-all.yml
```

Run one model family:

```bash
uv run --package rotunda-models rotunda-models train config/laptop-clicks.yml
uv run --package rotunda-models rotunda-models train config/laptop-keyboard.yml
```

Configure validation-based early stopping in YAML:

```yaml
training:
  epochs: 150
  early_stopping_patience: 12
```

Each run writes:

- `Training/runs/<kind>-<timestamp>/model.pt`
- `Training/runs/<kind>-<timestamp>/model-best.pt`
- `Training/runs/<kind>-<timestamp>/metrics.jsonl`

The console logs show the main training stages: loading recordings, building
episodes, splitting data, initializing the model, training epochs, and saving
the checkpoint.

## Weights & Biases Tracking

W&B tracking is enabled in the laptop training configs. Log in before training:

```bash
wandb login
```

Log a single training run to W&B:

```yaml
wandb:
  enabled: true
  project: cadence-models
```

Runs still write local checkpoints and `metrics.jsonl`. W&B also logs training
metrics, optional model artifacts, and rollout diagnostics when validation data
is available.

Keep W&B logs local:

```yaml
wandb:
  enabled: true
  mode: offline
```

Skip model artifact uploads:

```yaml
wandb:
  enabled: true
  log_artifacts: false
```

Disable rollout diagnostics:

```yaml
clicks:
  wandb_click_rollout_examples: 0
keyboard:
  wandb_keyboard_rollout_examples: 0
```

Filter out very short keyboard examples for timing-focused training:

```yaml
keyboard:
  keyboard_min_final_length: 2
  keyboard_min_duration_ms: 1
```

## Generation

Generate a click trajectory:

```bash
rotunda-models generate-click \
  --checkpoint Training/runs/clicks-YYYYMMDD-HHMMSS/model.pt \
  --current-x 200 --current-y 400 \
  --dst-x 800 --dst-y 500
```

Click generation uses endpoint guidance by default because the destination is a
known input. The GRU predicts timing and movement increments while the decoder
keeps progress monotonic toward the destination and emits the final click at the
target. Pass `--no-endpoint-guidance` to inspect the raw free-running decoder.

Generate keyboard actions:

```bash
rotunda-models generate-keyboard \
  --checkpoint Training/runs/keyboard-YYYYMMDD-HHMMSS/model.pt \
  --final-string "hello"
```

By default, generated keyboard output reaches the requested final string.

Use the shortest valid edit path:

```bash
rotunda-models generate-keyboard \
  --checkpoint Training/runs/keyboard-YYYYMMDD-HHMMSS/model.pt \
  --final-string "hello" \
  --decode-mode canonical
```

New keyboard checkpoints learn wrong-character likelihood and wrong-character
choice from raw focused-text edits. Constrained decoding can emit those learned
wrong keys when the resulting text is still repairable, then returns to normal
structured decoding for the correction.

Inspect unconstrained model output:

```bash
rotunda-models generate-keyboard \
  --checkpoint Training/runs/keyboard-YYYYMMDD-HHMMSS/model.pt \
  --final-string "hello" \
  --decode-mode unconstrained
```

Generated rows are JSON objects with `offsetMs`, `dtMs`, the emitted action, and
a `stepKind` label.

## Runtime Export

Export trained checkpoints for the native runtime:

```bash
uv run --package rotunda-models rotunda-models export-runtime \
  --mouse-checkpoint Training/runs/clicks-YYYYMMDD-HHMMSS/model-best.pt \
  --keyboard-checkpoint Training/runs/keyboard-YYYYMMDD-HHMMSS/model-best.pt \
  --output-dir Training/runtime
```

For release packaging, write the fixed browser-bundle payload names directly:

```bash
uv run --package rotunda-models rotunda-models export-runtime \
  --mouse-checkpoint Training/runs/clicks-YYYYMMDD-HHMMSS/model-best.pt \
  --keyboard-checkpoint Training/runs/keyboard-YYYYMMDD-HHMMSS/model-best.pt \
  --final
```

`--final` writes to `browserbuild/bundle/runtime-models/`, which the package targets copy
into the browser bundle when present.

The command writes compact SafeTensors-compatible binary weight files plus a
`runtime-models.json` manifest. Browser builds that include
`browserbuild/bundle/runtime-models/` resolve these shipped artifacts automatically, so
profiles only need to enable humanization:

```json
{
  "humanize": {
    "enabled": true
  }
}
```

The `mouseModelPath` and `keyboardModelPath` profile fields remain optional
developer overrides for testing non-bundled artifacts.

## Debug Videos

Render validation examples with real and simulated actions overlaid:

```bash
rotunda-models-render-debug-videos
```

Outputs:

- `Training/debug_media/mouse_val_overlay.mp4`
- `Training/debug_media/keyboard_val_overlay.mp4`

## W&B Sweeps

Sweeps are defined in YAML and point at a normal training config through
`root_config`. Fixed changes live under `overrides`, and sampled ranges use
dotted config paths under `parameters`.

Example sweep spec:

```yaml
root_config: ../laptop-keyboard.yml
trials: 8
metric:
  name: best/composite
  goal: minimize
overrides:
  training.epochs: 20
parameters:
  training.lr:
    type: loguniform
    min: 0.0003
    max: 0.003
  keyboard.keyboard_typo_positive_weight:
    values: [4.0, 8.0, 12.0]
```

For keyboard sweeps, `best/composite` is the recommended optimization target.
It tracks an equal blend of:

- median per-key wait timing error
- median full-edit duration timing error
- median key-press budget error
- key action error rate
- typo behavior error (rate mismatch plus precision/recall when defined)

Run a keyboard sweep from the repository root:

```bash
uv run --package rotunda-models rotunda-models-sweep \
  config/sweeps/laptop-keyboard.yml
```

The repository includes [config/sweeps/laptop-keyboard.yml](../config/sweeps/laptop-keyboard.yml)
as a starting point for typo-focused keyboard tuning.

Create the sweep in W&B without launching a local agent:

```bash
uv run --package rotunda-models rotunda-models-sweep \
  config/sweeps/laptop-keyboard.yml --create-only
```

Preview the generated W&B sweep config without contacting W&B:

```bash
uv run --package rotunda-models rotunda-models-sweep \
  config/sweeps/laptop-keyboard.yml --dry-run
```
