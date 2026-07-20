#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import statistics
import struct
import threading
import time
from pathlib import Path
from typing import Any

import click
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "rotunda-150.0.1-beta.25"
BROWSERBUILD_DIR = ROOT / "browserbuild"
DEFAULT_MOUSE_MODEL = BROWSERBUILD_DIR / "bundle" / "runtime-models" / "mouse.safetensors"
DEFAULT_KEYBOARD_MODEL = BROWSERBUILD_DIR / "bundle" / "runtime-models" / "keyboard.safetensors"
DEFAULT_OUTPUT_ROOT = ROOT / "Training" / "debug_media"
DEFAULT_TEXT = "rotunda models ship"
DEFAULT_PARAGRAPH = (
    "Rotunda runtime models should make browser input look continuous. "
    "the pointer plans a path to each target, then the keyboard model types "
    "this longer paragraph as a sequence of timed edits."
)
DEFAULT_UNKNOWN_KEYBOARD_ACTION = "¤"

BUTTONS = {
    "preview": "#preview",
    "approve": "#approve",
    "flag": "#flag",
    "archive": "#archive",
    "submit": "#submit",
    "reset": "#reset",
}


def _existing_executable(path: Path) -> Path | None:
    if path.is_file() and os.access(path, os.X_OK):
        return path
    return None


def find_executable(explicit_path: Path | None) -> Path:
    if explicit_path:
        resolved = explicit_path.expanduser().resolve()
        executable = _existing_executable(resolved)
        if executable:
            return executable
        raise click.ClickException(f"Rotunda executable is not executable: {resolved}")

    env_path = os.getenv("ROTUNDA_EXECUTABLE_PATH")
    if env_path:
        executable = _existing_executable(Path(env_path).expanduser().resolve())
        if executable:
            return executable
        raise click.ClickException(f"ROTUNDA_EXECUTABLE_PATH is not executable: {env_path}")

    patterns = [
        "obj-*/dist/Rotunda.app/Contents/MacOS/rotunda",
        "obj-*/dist/Rotunda.app/Contents/MacOS/Rotunda",
        "obj-*/dist/Nightly.app/Contents/MacOS/firefox",
        "obj-*/dist/Firefox Nightly.app/Contents/MacOS/firefox",
        "obj-*/dist/bin/rotunda-bin",
        "obj-*/dist/bin/rotunda",
        "obj-*/dist/bin/firefox",
    ]
    for pattern in patterns:
        for candidate in sorted(SOURCE_DIR.glob(pattern)):
            executable = _existing_executable(candidate)
            if executable:
                return executable.resolve()

    raise click.ClickException(
        "Could not find a built Rotunda executable. Run `make build` first or pass --executable-path."
    )


def bundled_model_candidates(executable: Path, file_name: str) -> list[Path]:
    executable_dir = executable.parent
    return [
        executable_dir / "runtime-models" / file_name,
        executable_dir.parent / "Resources" / "runtime-models" / file_name,
        executable_dir.parent.parent / "runtime-models" / file_name,
        executable_dir.parent.parent.parent.parent / "Resources" / "runtime-models" / file_name,
    ]


def resolve_bundled_model(executable: Path, file_name: str) -> Path | None:
    for candidate in bundled_model_candidates(executable, file_name):
        if candidate.is_file():
            return candidate.resolve()
    return None


def read_runtime_metadata(model_path: Path) -> dict[str, Any]:
    data = model_path.read_bytes()
    if len(data) < 8:
        raise click.ClickException(f"Runtime model is too small to be SafeTensors: {model_path}")
    header_len = struct.unpack("<Q", data[:8])[0]
    header = json.loads(data[8 : 8 + header_len].decode("utf-8"))
    metadata_text = header.get("__metadata__", {}).get("rotunda_metadata")
    if not metadata_text:
        raise click.ClickException(f"Runtime model is missing rotunda metadata: {model_path}")
    return json.loads(metadata_text)


def assert_keyboard_text_supported(model_path: Path, texts: list[str]) -> None:
    metadata = read_runtime_metadata(model_path)
    action_to_id = metadata.get("actionToId")
    if metadata.get("kind") != "keyboard_action_gru" or not isinstance(action_to_id, dict):
        raise click.ClickException(f"Runtime model is not a keyboard action model: {model_path}")
    unknown_action = metadata.get("unknownAction", DEFAULT_UNKNOWN_KEYBOARD_ACTION)
    has_unknown_action = isinstance(unknown_action, str) and unknown_action in action_to_id
    missing = sorted(
        {char for text in texts for char in text if char not in action_to_id and not has_unknown_action},
        key=ord,
    )
    if missing:
        labels = ", ".join(repr(char) for char in missing)
        raise click.ClickException(
            f"Keyboard runtime model cannot emit required characters: {labels}. "
            f"Regenerate {model_path} with those concrete actions or the unknown-action stand-in."
        )


DEMO_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Rotunda Runtime Model Demo</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; }
    body { margin: 0; background: #f1f5f7; color: #16212b; }
    main { min-height: 100vh; padding: 28px; box-sizing: border-box; }
    .workspace { max-width: 1180px; margin: 0 auto; display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 18px; align-items: start; }
    .panel, .rail { background: white; border: 1px solid #d8e0e6; border-radius: 8px; box-shadow: 0 18px 60px rgb(25 40 55 / 10%); }
    .panel { padding: 24px; }
    .rail { padding: 18px; position: sticky; top: 18px; }
    h1 { margin: 0 0 18px; font-size: 24px; line-height: 1.2; }
    h2 { margin: 0 0 12px; font-size: 15px; line-height: 1.25; color: #334957; }
    .field { margin-top: 18px; }
    label { display: block; font-size: 13px; font-weight: 650; color: #4b5a66; margin-bottom: 8px; }
    textarea { width: 100%; box-sizing: border-box; resize: vertical; border: 1px solid #aab8c3; border-radius: 6px; padding: 12px; font: 16px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; outline-color: #175b9f; background: #fbfdfe; }
    #message { min-height: 96px; }
    #paragraph { min-height: 210px; }
    .button-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
    button { border: 0; background: #175b9f; color: white; border-radius: 6px; padding: 10px 12px; min-height: 40px; font-weight: 700; cursor: pointer; }
    button.secondary { background: #3e596b; }
    button.warning { background: #9b4d16; }
    button.danger { background: #9b2635; }
    output { display: block; color: #39505f; font-size: 14px; min-height: 22px; }
    .preview { min-height: 86px; border: 1px solid #d8e0e6; background: #f7fafb; border-radius: 6px; padding: 12px; font-size: 14px; line-height: 1.45; white-space: pre-wrap; }
    pre { height: 320px; overflow: auto; background: #101820; color: #d8f2ff; border-radius: 6px; padding: 12px; font-size: 12px; line-height: 1.4; white-space: pre-wrap; }
    .telemetry { margin-top: 12px; font-size: 12px; color: #5a6b78; display: flex; justify-content: space-between; gap: 8px; }
    .telemetry span { font-variant-numeric: tabular-nums; }
    @media (max-width: 900px) { .workspace { grid-template-columns: 1fr; } .rail { position: static; } }
  </style>
</head>
<body>
  <main>
    <div class="workspace">
      <section class="panel">
        <h1>Runtime Model Demo</h1>
        <div class="field">
          <label for="message">Short text field</label>
          <textarea id="message" autocomplete="off" spellcheck="false"></textarea>
        </div>
        <div class="field">
          <label for="paragraph">Long paragraph field</label>
          <textarea id="paragraph" autocomplete="off" spellcheck="false"></textarea>
        </div>
      </section>

      <aside class="rail">
        <h2>Actions</h2>
        <output id="status">Waiting for CLI command</output>
        <div class="button-grid">
          <button id="preview" type="button" class="secondary" data-action="preview">Preview</button>
          <button id="approve" type="button" data-action="approve">Approve</button>
          <button id="flag" type="button" class="warning" data-action="flag">Flag</button>
          <button id="archive" type="button" class="secondary" data-action="archive">Archive</button>
          <button id="submit" type="button" data-action="submit">Submit</button>
          <button id="reset" type="button" class="danger" data-action="reset">Reset</button>
        </div>
        <h2 style="margin-top: 18px;">Preview</h2>
        <div id="previewPane" class="preview"></div>
        <h2 style="margin-top: 18px;">Event log</h2>
        <pre id="log"></pre>
        <div class="telemetry">
          <span>Buffered: <span id="bufferCount">0</span></span>
          <span>Sent: <span id="sentCount">0</span></span>
          <span id="sendStatus">idle</span>
        </div>
      </aside>
    </div>
  </main>
  <script>
    window.__rotundaEvents = [];
    window.__rotundaActions = [];
    window.__rotundaPending = [];
    window.__rotundaSent = 0;
    const log = document.getElementById("log");
    const status = document.getElementById("status");
    const message = document.getElementById("message");
    const paragraph = document.getElementById("paragraph");
    const previewPane = document.getElementById("previewPane");
    const bufferCount = document.getElementById("bufferCount");
    const sentCount = document.getElementById("sentCount");
    const sendStatus = document.getElementById("sendStatus");

    const SESSION_ID = (typeof crypto !== "undefined" && crypto.randomUUID)
      ? crypto.randomUUID()
      : "session-" + Math.random().toString(36).slice(2);
    const SESSION_START = Date.now();
    const FLUSH_INTERVAL_MS = 250;
    const MAX_BATCH = 200;

    function targetValue(event) {
      const target = event.target;
      if (target && typeof target.value === "string")
        return target.value;
      return undefined;
    }

    function record(event) {
      const entry = {
        type: event.type,
        ts: performance.now(),
        eventTimeStamp: event.timeStamp,
        x: Number.isFinite(event.clientX) ? event.clientX : null,
        y: Number.isFinite(event.clientY) ? event.clientY : null,
        button: event.button,
        buttons: event.buttons,
        key: event.key,
        code: event.code,
        data: event.data,
        inputType: event.inputType,
        targetId: event.target && event.target.id || null,
        value: targetValue(event)
      };
      window.__rotundaEvents.push(entry);
      window.__rotundaPending.push(entry);
      const last = window.__rotundaEvents.slice(-28);
      log.textContent = last.map(item => JSON.stringify(item)).join("\\n");
      bufferCount.textContent = String(window.__rotundaPending.length);
    }

    let flushing = false;
    async function flush() {
      if (flushing || window.__rotundaPending.length === 0) return;
      flushing = true;
      const batch = window.__rotundaPending.splice(0, MAX_BATCH);
      bufferCount.textContent = String(window.__rotundaPending.length);
      sendStatus.textContent = "sending";
      try {
        const res = await fetch("/log", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: SESSION_ID,
            session_start: SESSION_START,
            received_at_client: Date.now(),
            entries: batch
          }),
          keepalive: true
        });
        if (res.ok) {
          window.__rotundaSent += batch.length;
          sentCount.textContent = String(window.__rotundaSent);
          sendStatus.textContent = "ok";
        } else {
          sendStatus.textContent = "http " + res.status;
          window.__rotundaPending.unshift(...batch);
          bufferCount.textContent = String(window.__rotundaPending.length);
        }
      } catch (err) {
        sendStatus.textContent = "error";
        window.__rotundaPending.unshift(...batch);
        bufferCount.textContent = String(window.__rotundaPending.length);
      } finally {
        flushing = false;
      }
    }

    setInterval(flush, FLUSH_INTERVAL_MS);
    window.addEventListener("beforeunload", () => {
      if (window.__rotundaPending.length === 0) return;
      const blob = new Blob([JSON.stringify({
        session_id: SESSION_ID,
        session_start: SESSION_START,
        received_at_client: Date.now(),
        entries: window.__rotundaPending,
        final: true
      })], { type: "application/json" });
      try { navigator.sendBeacon("/log", blob); } catch (_) {}
    });

    for (const type of ["mousemove", "mousedown", "mouseup", "click"]) {
      window.addEventListener(type, record, true);
    }
    for (const type of ["keydown", "keyup", "beforeinput", "input", "compositionstart", "compositionupdate", "compositionend"]) {
      document.addEventListener(type, record, true);
    }

    for (const button of document.querySelectorAll("button[data-action]")) {
      button.addEventListener("click", () => {
        const action = button.dataset.action;
        const details = {
          action,
          ts: performance.now(),
          shortLength: message.value.length,
          paragraphLength: paragraph.value.length
        };
        if (action === "preview") {
          previewPane.textContent = [message.value, paragraph.value].filter(Boolean).join("\\n\\n");
          status.textContent = `Previewed ${details.shortLength + details.paragraphLength} chars`;
        } else if (action === "reset") {
          message.value = "";
          paragraph.value = "";
          previewPane.textContent = "";
          status.textContent = "Cleared both fields";
        } else {
          status.textContent = `${button.textContent} clicked after ${details.shortLength + details.paragraphLength} chars`;
        }
        window.__rotundaActions.push(details);
      });
    }

    window.__rotundaFlush = flush;
    window.__rotundaSnapshot = () => ({
      message: message.value,
      paragraph: paragraph.value,
      status: status.textContent,
      preview: previewPane.textContent,
      actionCount: window.__rotundaActions.length,
      pendingCount: window.__rotundaPending.length,
      sentCount: window.__rotundaSent
    });
  </script>
</body>
</html>
"""


def write_demo_page(output_dir: Path) -> Path:
    page_path = output_dir / "runtime-model-demo.html"
    page_path.write_text(DEMO_HTML, encoding="utf-8")
    return page_path


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def build_app(html: str, log_path: Path) -> tuple[FastAPI, threading.Lock, dict[str, int]]:
    app = FastAPI(title="rotunda-runtime-model-demo")
    write_lock = threading.Lock()
    counters = {"batches": 0, "entries": 0}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(html)

    @app.post("/log")
    async def collect(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:
            return JSONResponse({"error": f"invalid json: {exc}"}, status_code=400)
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return JSONResponse({"error": "entries must be a list"}, status_code=400)
        envelope = {
            "received_at_server": time.time(),
            "session_id": payload.get("session_id"),
            "session_start": payload.get("session_start"),
            "received_at_client": payload.get("received_at_client"),
            "final": bool(payload.get("final")),
            "entries": entries,
        }
        line = json.dumps(envelope, separators=(",", ":")) + "\n"
        with write_lock:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            counters["batches"] += 1
            counters["entries"] += len(entries)
        return JSONResponse({"received": len(entries), "total_entries": counters["entries"]})

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, **counters})

    return app, write_lock, counters


def start_log_server(
    html: str, log_path: Path, port: int | None = None
) -> tuple[uvicorn.Server, threading.Thread, str, dict[str, int]]:
    app, _lock, counters = build_app(html, log_path)
    bound_port = port or _pick_free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=bound_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="rotunda-log-server", daemon=True)
    thread.start()
    deadline = time.time() + 10.0
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise click.ClickException("Log server failed to start within 10 seconds.")
    return server, thread, f"http://127.0.0.1:{bound_port}", counters


def summarize(
    events: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    mouse_events = [
        event
        for event in events
        if event.get("type") in {"mousemove", "mousedown", "mouseup", "click"}
    ]
    move_events = [event for event in events if event.get("type") == "mousemove"]
    input_events = [event for event in events if event.get("type") == "input"]
    key_events = [event for event in events if event.get("type") in {"keydown", "keyup"}]
    button_clicks = [
        event
        for event in mouse_events
        if event.get("type") == "click" and event.get("targetId") in BUTTONS
    ]
    value_change_input_events: list[dict[str, Any]] = []
    last_value: str | None = None
    for event in input_events:
        value = event.get("value")
        if not isinstance(value, str) or value == last_value:
            continue
        value_change_input_events.append(event)
        last_value = value

    coords = {
        (round(event["x"], 2), round(event["y"], 2))
        for event in move_events
        if event.get("x") is not None and event.get("y") is not None
    }
    input_intervals = [
        round(value_change_input_events[index]["ts"] - value_change_input_events[index - 1]["ts"], 3)
        for index in range(1, len(value_change_input_events))
    ]
    mouse_duration = 0.0
    if len(mouse_events) >= 2:
        mouse_duration = round(mouse_events[-1]["ts"] - mouse_events[0]["ts"], 3)

    return {
        "snapshot": snapshot,
        "event_count": len(events),
        "mouse": {
            "event_count": len(mouse_events),
            "mousemove_count": len(move_events),
            "unique_mousemove_coords": len(coords),
            "button_click_count": len(button_clicks),
            "duration_ms": mouse_duration,
            "first": mouse_events[0] if mouse_events else None,
            "last": mouse_events[-1] if mouse_events else None,
        },
        "keyboard": {
            "input_event_count": len(input_events),
            "value_change_input_count": len(value_change_input_events),
            "key_event_count": len(key_events),
            "input_interval_ms": input_intervals,
            "input_interval_median_ms": round(statistics.median(input_intervals), 3)
            if input_intervals
            else None,
        },
        "actions": {
            "count": len(actions),
            "items": actions,
        },
    }


def click_selector(page: Any, selector: str, label: str) -> None:
    locator = page.locator(selector)
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    if not box:
        raise click.ClickException(f"Demo target has no bounding box: {label}")
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    page.mouse.click(x, y)


def type_into(page: Any, selector: str, text: str, label: str) -> None:
    click_selector(page, selector, label)
    page.keyboard.insert_text(text)


def run_scripted_sequence(page: Any, short_text: str, paragraph_text: str) -> None:
    type_into(page, "#message", short_text, "short text field")
    type_into(page, "#paragraph", paragraph_text, "long paragraph field")
    for button in ["preview", "approve", "flag", "archive", "submit"]:
        click_selector(page, BUTTONS[button], button)


def print_commands() -> None:
    click.echo(
        "\nCommands:\n"
        "  [1] run                         type both fields, then click preview/approve/flag/archive/submit\n"
        "  [2] short custom text           click the short field and type custom text\n"
        "  [3] paragraph custom text       click the paragraph field and type custom text\n"
        "  [4] click preview               click one button: preview approve flag archive submit reset\n"
        "  [5] screenshot optional-name    save a screenshot in the output directory\n"
        "  [6] summary                     print current field values and event counts\n"
        "  [7] events optional-count       print the last recorded browser events\n"
        "  [8] help                        show this menu\n"
        "  [9] quit                        write the report and close the browser\n"
    )


def browser_snapshot(page: Any) -> dict[str, Any]:
    return page.evaluate("window.__rotundaSnapshot()")


def command_loop(
    page: Any,
    output_dir: Path,
    short_text: str,
    paragraph_text: str,
) -> None:
    print_commands()
    while True:
        try:
            raw = input("runtime-demo [1-9]> ").strip()
        except EOFError:
            click.echo()
            return
        if not raw:
            continue

        command, _, rest = raw.partition(" ")
        command = command.lower()
        rest = rest.strip()
        numeric_commands = {
            "1": "run",
            "2": "short",
            "3": "paragraph",
            "4": "click",
            "5": "screenshot",
            "6": "summary",
            "7": "events",
            "8": "help",
            "9": "quit",
        }
        command = numeric_commands.get(command, command)

        if command in {"q", "quit", "exit"}:
            return
        if command in {"h", "help", "?"}:
            print_commands()
            continue
        if command == "run":
            run_scripted_sequence(page, short_text, paragraph_text)
            click.echo("Ran the default two-field and button sequence.")
            continue
        if command in {"short", "first"}:
            type_into(page, "#message", rest or short_text, "short text field")
            continue
        if command in {"paragraph", "para", "second"}:
            type_into(page, "#paragraph", rest or paragraph_text, "long paragraph field")
            continue
        if command == "click":
            button = rest.lower()
            if button not in BUTTONS:
                click.echo(f"Unknown button {button!r}. Choices: {', '.join(BUTTONS)}")
                continue
            click_selector(page, BUTTONS[button], button)
            continue
        if command == "screenshot":
            name = rest or f"runtime-model-demo-{time.strftime('%H%M%S')}.png"
            if not name.endswith(".png"):
                name += ".png"
            screenshot_path = output_dir / name
            page.screenshot(path=str(screenshot_path), full_page=True)
            click.echo(f"Wrote {screenshot_path}")
            continue
        if command == "summary":
            events = page.evaluate("window.__rotundaEvents")
            actions = page.evaluate("window.__rotundaActions")
            click.echo(json.dumps(summarize(events, actions, browser_snapshot(page)), indent=2))
            continue
        if command == "events":
            try:
                count = int(rest) if rest else 12
            except ValueError:
                click.echo("events expects an integer count.")
                continue
            events = page.evaluate("window.__rotundaEvents")
            click.echo(json.dumps(events[-count:], indent=2))
            continue

        click.echo(f"Unknown command {command!r}. Type help for commands.")


@click.command()
@click.option("--executable-path", type=click.Path(path_type=Path), default=None)
@click.option("--mouse-model", type=click.Path(path_type=Path), default=DEFAULT_MOUSE_MODEL)
@click.option("--keyboard-model", type=click.Path(path_type=Path), default=DEFAULT_KEYBOARD_MODEL)
@click.option("--text", default=DEFAULT_TEXT, show_default=True)
@click.option("--paragraph-text", default=DEFAULT_PARAGRAPH, show_default=True)
@click.option("--output-root", type=click.Path(path_type=Path), default=DEFAULT_OUTPUT_ROOT)
@click.option("--headless", is_flag=True, help="Run without opening a visible browser window.")
@click.option("--keep-open", is_flag=True, help="Leave the browser open until Enter is pressed.")
@click.option("--pause-seconds", type=float, default=2.0, show_default=True)
@click.option("--scripted-start", is_flag=True, help="Run the default sequence before the command loop.")
@click.option("--no-interactive", is_flag=True, help="Run once and exit after the scripted sequence.")
@click.option(
    "--use-bundled-models",
    is_flag=True,
    help="Do not put model paths in the profile; let the browser resolve shipped runtime-models.",
)
@click.option(
    "--server-port",
    type=int,
    default=0,
    show_default=True,
    help="Port to bind the localhost log server on. 0 picks a free ephemeral port.",
)
def main(
    executable_path: Path | None,
    mouse_model: Path,
    keyboard_model: Path,
    text: str,
    paragraph_text: str,
    output_root: Path,
    headless: bool,
    keep_open: bool,
    pause_seconds: float,
    scripted_start: bool,
    no_interactive: bool,
    use_bundled_models: bool,
    server_port: int,
) -> None:
    executable = find_executable(executable_path)
    mouse_model = mouse_model.expanduser().resolve()
    keyboard_model = keyboard_model.expanduser().resolve()
    bundled_mouse_model: Path | None = None
    bundled_keyboard_model: Path | None = None
    if use_bundled_models:
        bundled_mouse_model = resolve_bundled_model(executable, "mouse.safetensors")
        bundled_keyboard_model = resolve_bundled_model(executable, "keyboard.safetensors")
        missing = []
        if bundled_mouse_model is None:
            missing.append("mouse.safetensors")
        if bundled_keyboard_model is None:
            missing.append("keyboard.safetensors")
        if missing:
            labels = ", ".join(missing)
            raise click.ClickException(
                f"Bundled runtime model(s) not found for {executable}: {labels}. "
                "Run without --use-bundled-models to pass explicit model paths, or package the app bundle first."
            )
        assert bundled_mouse_model is not None
        assert bundled_keyboard_model is not None
        assert_keyboard_text_supported(bundled_keyboard_model, [text, paragraph_text])
    else:
        for model_path in [mouse_model, keyboard_model]:
            if not model_path.is_file():
                raise click.ClickException(f"Model file does not exist: {model_path}")
        assert_keyboard_text_supported(keyboard_model, [text, paragraph_text])

    output_dir = output_root.expanduser().resolve() / f"runtime-browser-demo-{time.strftime('%Y%m%d-%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    page_path = write_demo_page(output_dir)
    log_path = output_dir / "browser-events.jsonl"
    log_path.touch()
    config_path = output_dir / "rotunda-runtime-model-profile.json"
    config = {
        "debug": True,
        "humanize": {
            "enabled": True,
        },
    }
    if not use_bundled_models:
        config["humanize"]["mouseModelPath"] = str(mouse_model)
        config["humanize"]["keyboardModelPath"] = str(keyboard_model)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    env = {**os.environ, "ROTUNDA_CONFIG_PATH": str(config_path)}
    click.echo(f"Launching {executable}")
    click.echo(f"Writing demo artifacts to {output_dir}")

    server, server_thread, base_url, counters = start_log_server(
        DEMO_HTML, log_path, port=server_port or None
    )
    click.echo(f"Log server listening at {base_url} (browser logs -> {log_path})")

    try:
        with sync_playwright() as playwright:
            browser = playwright.firefox.launch(
                executable_path=str(executable),
                headless=headless,
                env=env,
            )
            page = browser.new_page(viewport={"width": 1024, "height": 760})
            page.goto(base_url + "/")

            if scripted_start or no_interactive:
                run_scripted_sequence(page, text, paragraph_text)

            if no_interactive:
                if pause_seconds > 0:
                    page.wait_for_timeout(int(pause_seconds * 1000))
            else:
                command_loop(page, output_dir, text, paragraph_text)

            if keep_open and no_interactive:
                click.echo("Browser is open. Press Enter to close it.")
                input()

            try:
                page.evaluate("window.__rotundaFlush && window.__rotundaFlush()")
                page.wait_for_timeout(400)
            except Exception:
                pass

            events = page.evaluate("window.__rotundaEvents")
            actions = page.evaluate("window.__rotundaActions")
            snapshot = browser_snapshot(page)
            screenshot_path = output_dir / "runtime-model-demo.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        server_thread.join(timeout=5.0)

    summary = summarize(events, actions, snapshot)
    report = {
        "executable": str(executable),
        "config": str(config_path),
        "page": str(page_path),
        "screenshot": str(screenshot_path),
        "log_server": {
            "url": base_url,
            "log_path": str(log_path),
            "batches_received": counters["batches"],
            "entries_received": counters["entries"],
        },
        "models": {
            "source": "bundled" if use_bundled_models else "profile",
            "mouse": str(bundled_mouse_model or mouse_model),
            "keyboard": str(bundled_keyboard_model or keyboard_model),
        },
        "summary": summary,
        "events": events,
        "actions": actions,
    }
    report_path = output_dir / "runtime-model-demo-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if no_interactive and snapshot.get("message") != text:
        raise click.ClickException(
            f"Final short text mismatch: expected {text!r}, got {snapshot.get('message')!r}"
        )
    if no_interactive and snapshot.get("paragraph") != paragraph_text:
        raise click.ClickException(
            "Final paragraph text mismatch: "
            f"expected {paragraph_text!r}, got {snapshot.get('paragraph')!r}"
        )
    if no_interactive and summary["mouse"]["mousemove_count"] < 2:
        raise click.ClickException("Mouse demo did not emit enough mousemove events")
    if no_interactive and summary["keyboard"]["value_change_input_count"] < 1:
        raise click.ClickException("Keyboard demo did not emit value-changing input events")
    if (
        no_interactive
        and use_bundled_models
        and summary["keyboard"]["input_interval_median_ms"] is not None
        and summary["keyboard"]["input_interval_median_ms"] < 25.0
    ):
        raise click.ClickException(
            "Keyboard cadence looks like the fixed fallback path, not the runtime model "
            f"(median interval {summary['keyboard']['input_interval_median_ms']} ms)."
        )

    click.echo(
        json.dumps(
            {
                "summary": summary,
                "report": str(report_path),
                "browser_log": str(log_path),
                "browser_log_batches": counters["batches"],
                "browser_log_entries": counters["entries"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
