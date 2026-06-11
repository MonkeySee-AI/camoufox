from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from ._generated_profile import RotundaProfile
from .settings import (
    ROTUNDA_VM_ACCESS_BUFFERED,
    ROTUNDA_VM_ACCESS_LOG,
    ROTUNDA_VM_ACCESS_LOG_FILE,
    ROTUNDA_VM_ACCESS_REALM,
    ROTUNDA_VM_ACCESS_RETURNS,
    RotundaSettings,
    env_snapshot,
)

DEFAULT_SECTIONS = {
    "manifest",
    "network",
    "console",
    "vm",
    "returns",
}
SECTION_ALIASES = {
    "all": DEFAULT_SECTIONS,
    "js": {"vm", "returns"},
}
SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
}
SENSITIVE_KEY_PARTS = ("password", "passwd", "secret", "token", "authorization", "cookie")
SECRET_RE = re.compile(
    r"\b(bearer|basic)\s+[a-z0-9._~+/=-]+|"
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?id)=([^&\s]+)",
    re.IGNORECASE,
)
class DebugDump:
    def __init__(
        self,
        directory: Path,
        sections: set[str],
        *,
        max_body_bytes: int = 1_048_576,
        raw: bool = False,
    ) -> None:
        self.directory = directory
        self.sections = sections
        self.max_body_bytes = max_body_bytes
        self.raw = raw
        self._lock = threading.RLock()
        self._event_id = 0
        self.directory.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, env: dict[str, Any] | None = None) -> DebugDump | None:
        settings = RotundaSettings.from_env(env)
        directory = settings.debug_dump_dir
        if not directory:
            return None

        sections_value = settings.debug_dump or "all"
        sections = _parse_sections(sections_value)
        if not sections:
            return None

        return cls(
            Path(directory).expanduser().resolve(),
            sections,
            max_body_bytes=settings.debug_dump_max_body,
            raw=settings.debug_dump_raw,
        )

    def enabled(self, section: str) -> bool:
        return section in self.sections

    def next_event_id(self) -> int:
        with self._lock:
            self._event_id += 1
            return self._event_id

    def path(self, filename: str) -> Path:
        return self.directory / filename

    def append_jsonl(self, filename: str, event: dict[str, Any]) -> None:
        payload = {
            "event_id": self.next_event_id(),
            "timestamp": time.time(),
            **event,
        }
        line = json.dumps(_jsonable(payload, raw=self.raw), sort_keys=True, ensure_ascii=False)
        with self._lock, self.path(filename).open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")

    def update_manifest(self, section: str, data: dict[str, Any]) -> None:
        if not self.enabled("manifest"):
            return

        path = self.path("manifest.json")
        with self._lock:
            if path.exists():
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    manifest = {}
            else:
                manifest = {}

            manifest.setdefault("created_at", time.time())
            manifest["updated_at"] = time.time()
            manifest[section] = _jsonable(data, raw=self.raw)
            path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )


def configure_launch_debug_dump(
    env: dict[str, Any],
    *,
    executable_path: str | Path,
    firefox_user_prefs: dict[str, Any],
    config: RotundaProfile,
) -> None:
    dump = DebugDump.from_env(env)
    if not dump:
        return

    executable = Path(executable_path)
    if dump.enabled("vm"):
        env.setdefault(ROTUNDA_VM_ACCESS_LOG, "1")
        env.setdefault(ROTUNDA_VM_ACCESS_LOG_FILE, str(dump.path("vm-access.log")))
        env.setdefault(ROTUNDA_VM_ACCESS_BUFFERED, "1")
        env.setdefault(ROTUNDA_VM_ACCESS_REALM, "1")
    if dump.enabled("returns"):
        env.setdefault(ROTUNDA_VM_ACCESS_RETURNS, "1")

    dump.update_manifest(
        "launch",
        {
            "executable_path": str(executable),
            "executable": _file_fingerprint(executable),
            "xul": _xul_fingerprints(executable),
            "firefox_user_prefs": firefox_user_prefs,
            "config": config.model_dump(by_alias=True, exclude_none=True, mode="json"),
            "env": env_snapshot(env),
        },
    )


def attach_debug_metadata(target: Any, launch_options: dict[str, Any]) -> Any:
    env = launch_options.get("env") if isinstance(launch_options, dict) else None
    if env:
        try:
            target._rotunda_debug_dump_env = dict(env)
            target._rotunda_debug_dump_launch_options = _jsonable(launch_options)
        except Exception:
            pass
    return target


def install_sync_context_debug_dump(
    context: Any,
    *,
    browser: Any,
    fingerprint_payload: dict[str, Any],
    context_options: dict[str, Any],
) -> None:
    dump = _dump_for_target(browser)
    if not dump:
        return

    _write_context_manifest(dump, browser, fingerprint_payload, context_options)

    if dump.enabled("console"):
        _install_sync_console_dump(context, dump)
    if dump.enabled("network"):
        _install_sync_network_dump(context, dump)


async def install_async_context_debug_dump(
    context: Any,
    *,
    browser: Any,
    fingerprint_payload: dict[str, Any],
    context_options: dict[str, Any],
) -> None:
    dump = _dump_for_target(browser)
    if not dump:
        return

    _write_context_manifest(dump, browser, fingerprint_payload, context_options)

    if dump.enabled("console"):
        _install_async_console_dump(context, dump)
    if dump.enabled("network"):
        _install_async_network_dump(context, dump)


def _dump_for_target(target: Any) -> DebugDump | None:
    env = getattr(target, "_rotunda_debug_dump_env", None)
    return DebugDump.from_env(env if isinstance(env, dict) else None)


def _write_context_manifest(
    dump: DebugDump,
    browser: Any,
    fingerprint_payload: dict[str, Any],
    context_options: dict[str, Any],
) -> None:
    config = fingerprint_payload.get("config")
    dump.update_manifest(
        "context",
        {
            "browser_version": _get_attr_or_call(browser, "version"),
            "context_options": context_options,
            "init_script": {
                "bytes": len(str(fingerprint_payload.get("init_script", "")).encode("utf-8")),
                "sha256": _sha256_bytes(
                    str(fingerprint_payload.get("init_script", "")).encode("utf-8")
                ),
            },
            "fingerprint_config": (
                config.model_dump(by_alias=True, exclude_none=True, mode="json")
                if isinstance(config, RotundaProfile)
                else config
            ),
        },
    )


def _install_sync_network_dump(context: Any, dump: DebugDump) -> None:
    request_ids: dict[int, str] = {}

    def request_id(request: Any) -> str:
        key = id(request)
        if key not in request_ids:
            request_ids[key] = f"req-{len(request_ids) + 1}"
        return request_ids[key]

    def on_request(request: Any) -> None:
        dump.append_jsonl("network.jsonl", _request_event("request", request_id(request), request, dump))

    def on_request_finished(request: Any) -> None:
        event = _request_event("requestfinished", request_id(request), request, dump)
        response = _safe_call(request, "response")
        if response is not None:
            event["response"] = _sync_response_record(response, dump)
        dump.append_jsonl("network.jsonl", event)

    def on_request_failed(request: Any) -> None:
        event = _request_event("requestfailed", request_id(request), request, dump)
        event["failure"] = _safe_call(request, "failure")
        dump.append_jsonl("network.jsonl", event)

    context.on("request", on_request)
    context.on("requestfinished", on_request_finished)
    context.on("requestfailed", on_request_failed)


def _install_async_network_dump(context: Any, dump: DebugDump) -> None:
    request_ids: dict[int, str] = {}
    pending_tasks: set[asyncio.Task[Any]] = set()

    def request_id(request: Any) -> str:
        key = id(request)
        if key not in request_ids:
            request_ids[key] = f"req-{len(request_ids) + 1}"
        return request_ids[key]

    def on_request(request: Any) -> None:
        dump.append_jsonl("network.jsonl", _request_event("request", request_id(request), request, dump))

    def on_request_finished(request: Any) -> None:
        task = asyncio.create_task(_async_dump_request_finished(request, request_id(request), dump))
        pending_tasks.add(task)
        task.add_done_callback(pending_tasks.discard)

    def on_request_failed(request: Any) -> None:
        event = _request_event("requestfailed", request_id(request), request, dump)
        event["failure"] = _safe_call(request, "failure")
        dump.append_jsonl("network.jsonl", event)

    context.on("request", on_request)
    context.on("requestfinished", on_request_finished)
    context.on("requestfailed", on_request_failed)


async def _async_dump_request_finished(request: Any, request_id: str, dump: DebugDump) -> None:
    event = _request_event("requestfinished", request_id, request, dump)
    response = await _maybe_await(_safe_call(request, "response"))
    if response is not None:
        event["response"] = await _async_response_record(response, dump)
    dump.append_jsonl("network.jsonl", event)


def _request_event(event_type: str, request_id: str, request: Any, dump: DebugDump) -> dict[str, Any]:
    frame = _safe_getattr(request, "frame")
    redirected_from = _safe_call(request, "redirected_from")
    redirected_to = _safe_call(request, "redirected_to")
    return {
        "type": event_type,
        "request_id": request_id,
        "url": _safe_getattr(request, "url"),
        "method": _safe_getattr(request, "method"),
        "resource_type": _safe_getattr(request, "resource_type"),
        "is_navigation_request": _safe_call(request, "is_navigation_request"),
        "frame_url": _safe_getattr(frame, "url") if frame is not None else None,
        "headers": _redact_headers(_safe_getattr(request, "headers") or {}, raw=dump.raw),
        "post_data": _body_record(_safe_getattr(request, "post_data"), dump),
        "timing": _safe_getattr(request, "timing"),
        "redirected_from": _safe_getattr(redirected_from, "url") if redirected_from else None,
        "redirected_to": _safe_getattr(redirected_to, "url") if redirected_to else None,
    }


def _sync_response_record(response: Any, dump: DebugDump) -> dict[str, Any]:
    body: Any = None
    body_error: str | None = None
    try:
        body = response.body()
    except Exception as exc:
        body_error = f"{type(exc).__name__}: {exc}"

    return {
        "url": _safe_getattr(response, "url"),
        "status": _safe_getattr(response, "status"),
        "status_text": _safe_getattr(response, "status_text"),
        "headers": _redact_headers(_safe_getattr(response, "headers") or {}, raw=dump.raw),
        "body": _body_record(body, dump),
        "body_error": body_error,
    }


async def _async_response_record(response: Any, dump: DebugDump) -> dict[str, Any]:
    body: Any = None
    body_error: str | None = None
    try:
        body = await response.body()
    except Exception as exc:
        body_error = f"{type(exc).__name__}: {exc}"

    return {
        "url": _safe_getattr(response, "url"),
        "status": _safe_getattr(response, "status"),
        "status_text": _safe_getattr(response, "status_text"),
        "headers": _redact_headers(_safe_getattr(response, "headers") or {}, raw=dump.raw),
        "body": _body_record(body, dump),
        "body_error": body_error,
    }


def _install_sync_console_dump(context: Any, dump: DebugDump) -> None:
    def attach_page(page: Any) -> None:
        page.on("console", lambda message: _dump_console_message(dump, page, message))
        page.on("pageerror", lambda error: _dump_page_error(dump, page, error))

    for page in _safe_getattr(context, "pages") or []:
        attach_page(page)
    context.on("page", attach_page)


def _install_async_console_dump(context: Any, dump: DebugDump) -> None:
    def attach_page(page: Any) -> None:
        page.on("console", lambda message: _dump_console_message(dump, page, message))
        page.on("pageerror", lambda error: _dump_page_error(dump, page, error))

    for page in _safe_getattr(context, "pages") or []:
        attach_page(page)
    context.on("page", attach_page)


def _dump_console_message(dump: DebugDump, page: Any, message: Any) -> None:
    text = _safe_getattr(message, "text")
    if not dump.enabled("console"):
        return

    dump.append_jsonl(
        "console.jsonl",
        {
            "type": "console",
            "page_url": _safe_getattr(page, "url"),
            "console_type": _safe_getattr(message, "type"),
            "text": text,
            "location": _safe_getattr(message, "location"),
            "args": [_js_handle_preview(arg) for arg in (_safe_getattr(message, "args") or [])],
        },
    )


def _dump_page_error(dump: DebugDump, page: Any, error: Any) -> None:
    if not dump.enabled("console"):
        return
    dump.append_jsonl(
        "console.jsonl",
        {
            "type": "pageerror",
            "page_url": _safe_getattr(page, "url"),
            "message": str(error),
            "stack": getattr(error, "stack", None),
        },
    )


def _parse_sections(value: str) -> set[str]:
    sections: set[str] = set()
    for part in value.split(","):
        section = part.strip().lower()
        if not section:
            continue
        if section in SECTION_ALIASES:
            sections.update(SECTION_ALIASES[section])
        else:
            sections.add(section)
    if "returns" in sections:
        sections.add("vm")
    return sections


def _file_fingerprint(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "exists": False}

    return {
        "path": str(path),
        "exists": True,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "sha256": _sha256_file(path),
    }


def _xul_fingerprints(executable: Path) -> dict[str, Any]:
    candidates: dict[str, Path] = {
        "app_bundle": executable.parent / "XUL",
    }
    try:
        dist_dir = executable.parents[3]
        candidates["dist_bin"] = dist_dir / "bin" / "XUL"
    except IndexError:
        pass
    return {name: _file_fingerprint(path) for name, path in candidates.items()}


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _body_record(value: Any, dump: DebugDump) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        data = value.encode("utf-8", errors="replace")
        text = value
    elif isinstance(value, bytes):
        data = value
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            text = None
    else:
        text = str(value)
        data = text.encode("utf-8", errors="replace")

    truncated = len(data) > dump.max_body_bytes
    limited = data[: dump.max_body_bytes]
    record: dict[str, Any] = {
        "size": len(data),
        "sha256": _sha256_bytes(data),
        "truncated": truncated,
    }
    if text is not None and _looks_textual(limited):
        record["text"] = _redact_string(limited.decode("utf-8", errors="replace"), raw=dump.raw)
    else:
        record["base64"] = base64.b64encode(limited).decode("ascii")
    return record


def _looks_textual(data: bytes) -> bool:
    if not data:
        return True
    return data.count(b"\x00") == 0


def _redact_headers(headers: dict[str, Any], *, raw: bool) -> dict[str, Any]:
    if raw:
        return dict(headers)
    return {
        key: "<redacted>" if key.lower() in SENSITIVE_HEADER_NAMES else _redact_string(value, raw=raw)
        for key, value in dict(headers).items()
    }


def _redact_string(value: Any, *, raw: bool) -> Any:
    if raw or not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        auth_scheme = match.group(1)
        if auth_scheme:
            return f"{auth_scheme} <redacted>"
        key = match.group(2) or "secret"
        return f"{key}=<redacted>"

    return SECRET_RE.sub(replace, value)


def _jsonable(value: Any, *, raw: bool = False) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _redact_string(value, raw=raw)
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "size": len(value),
            "sha256": _sha256_bytes(value),
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            string_key = str(key)
            if not raw and any(part in string_key.lower() for part in SENSITIVE_KEY_PARTS):
                out[string_key] = "<redacted>"
            else:
                out[string_key] = _jsonable(item, raw=raw)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item, raw=raw) for item in value]
    if isinstance(value, RotundaProfile):
        return value.model_dump(by_alias=True, exclude_none=True, mode="json")
    try:
        json.dumps(value)
        return value
    except Exception:
        return repr(value)


def _safe_getattr(target: Any, name: str, default: Any = None) -> Any:
    try:
        value = getattr(target, name, default)
        if callable(value) and name in {"headers", "post_data", "timing", "pages", "args"}:
            return value()
        return value
    except Exception:
        return default


def _get_attr_or_call(target: Any, name: str, default: Any = None) -> Any:
    try:
        value = getattr(target, name, default)
        return value() if callable(value) else value
    except Exception:
        return default


def _safe_call(target: Any, name: str, *args: Any) -> Any:
    try:
        value = getattr(target, name)
        return value(*args)
    except Exception:
        return None


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _js_handle_preview(handle: Any) -> dict[str, Any]:
    try:
        value = handle.json_value()
        return {"json": _jsonable(value)}
    except Exception:
        return {"repr": repr(handle)}
