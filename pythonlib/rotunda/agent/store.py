from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import (
    AUTH_FILE,
    DAEMON_FILE,
    PROFILES_DIR,
    RESOURCES_FILE,
    SESSIONS_DIR,
    ensure_agent_dirs,
)

RUNTIME_RESOURCE_KINDS = {"context", "page", "element", "download"}
STALE_DAEMON_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class AgentResource:
    idx: int
    kind: str
    id: str
    profile_id: str | None = None
    parent_id: str | None = None
    label: str = ""
    created_at: float = 0.0
    runtime_id: str | None = None


class AgentStore:
    def __init__(self, *, prune_stale: bool = True) -> None:
        ensure_agent_dirs()
        if prune_stale:
            self.prune_stale_runtime_state()

    def create_profile(
        self,
        *,
        name: str | None = None,
        headless: bool = False,
    ) -> dict[str, Any]:
        profile_id = f"prof_{uuid.uuid4().hex[:12]}"
        profile_dir = PROFILES_DIR / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile = {
            "id": profile_id,
            "name": name or profile_id,
            "browser": "rotunda",
            "headless": headless,
            "humanize": True,
            "created_at": time.time(),
            "profile_dir": str(profile_dir),
            "user_data_dir": str(profile_dir / "browser-data"),
        }
        self.save_profile(profile)
        return profile

    def load_profile(self, profile_id: str) -> dict[str, Any]:
        path = self.profile_path(profile_id)
        if not path.exists():
            raise KeyError(f"Unknown profile: {profile_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def save_profile(self, profile: dict[str, Any]) -> None:
        path = self.profile_path(str(profile["id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")

    def profile_path(self, profile_id: str) -> Path:
        return PROFILES_DIR / profile_id / "profile.json"

    def session_path(self, profile_id: str) -> Path:
        return SESSIONS_DIR / f"{profile_id}.json"

    def load_session(self, profile_id: str) -> dict[str, Any] | None:
        path = self.session_path(profile_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def save_session(self, profile_id: str, session: dict[str, Any]) -> None:
        path = self.session_path(profile_id)
        self._atomic_write_json(path, session, mode=0o600)

    def remove_session(self, profile_id: str) -> None:
        path = self.session_path(profile_id)
        if path.exists():
            path.unlink()

    def new_token(self) -> str:
        return secrets.token_urlsafe(24)

    def ensure_auth_token(self) -> str:
        try:
            data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
            token = str(data.get("token") or "")
            if token:
                with suppress(OSError):
                    os.chmod(AUTH_FILE, 0o600)
                return token
            with suppress(OSError):
                AUTH_FILE.unlink()
        except FileNotFoundError:
            pass
        except json.JSONDecodeError:
            with suppress(OSError):
                AUTH_FILE.unlink()

        token = self.new_token()
        payload = {"token": token, "created_at": time.time()}
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(AUTH_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return self.ensure_auth_token()
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        return token

    def load_daemon_record(self) -> dict[str, Any] | None:
        if not DAEMON_FILE.exists():
            return None
        try:
            data = json.loads(DAEMON_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def save_daemon_record(self, record: dict[str, Any]) -> None:
        self._atomic_write_json(DAEMON_FILE, record)

    def remove_daemon_record(self, *, instance_id: str | None = None) -> None:
        if not DAEMON_FILE.exists():
            return
        if instance_id is not None:
            current = self.load_daemon_record()
            if current and current.get("instance_id") != instance_id:
                return
        DAEMON_FILE.unlink(missing_ok=True)

    def daemon_record_is_fresh(self, record: dict[str, Any] | None = None, *, now: float | None = None) -> bool:
        record = record if record is not None else self.load_daemon_record()
        if not record:
            return False
        try:
            update_tick = float(record.get("update_tick") or 0)
        except (TypeError, ValueError):
            return False
        return (now if now is not None else time.time()) - update_tick <= STALE_DAEMON_SECONDS

    def prune_stale_runtime_state(self) -> None:
        record = self.load_daemon_record()
        if self.daemon_record_is_fresh(record):
            return
        if record or self._has_runtime_state():
            self.clear_runtime_state()

    def clear_runtime_state(self, *, keep_daemon_record: bool = False) -> None:
        self._clear_runtime_resources()
        if SESSIONS_DIR.exists():
            for path in SESSIONS_DIR.glob("*.json"):
                path.unlink(missing_ok=True)
        if not keep_daemon_record:
            DAEMON_FILE.unlink(missing_ok=True)

    def register(
        self,
        *,
        kind: str,
        id: str,
        profile_id: str | None = None,
        parent_id: str | None = None,
        label: str = "",
        runtime_id: str | None = None,
    ) -> AgentResource:
        state = self._load_resources()
        for raw in state["resources"]:
            if raw["kind"] == kind and raw["id"] == id:
                raw.update(
                    {
                        "profile_id": profile_id,
                        "parent_id": parent_id,
                        "label": label,
                        "runtime_id": runtime_id,
                    }
                )
                self._save_resources(state)
                return self._resource_from_raw(raw)

        idx = int(state["next_idx"])
        state["next_idx"] = idx + 1
        raw = {
            "idx": idx,
            "kind": kind,
            "id": id,
            "profile_id": profile_id,
            "parent_id": parent_id,
            "label": label,
            "created_at": time.time(),
            "runtime_id": runtime_id,
        }
        state["resources"].append(raw)
        self._save_resources(state)
        return self._resource_from_raw(raw)

    def remove_children(self, parent_id: str, *, kind: str | None = None) -> None:
        state = self._load_resources()
        original_count = len(state["resources"])
        state["resources"] = [
            raw
            for raw in state["resources"]
            if not (raw.get("parent_id") == parent_id and (kind is None or raw.get("kind") == kind))
        ]
        if len(state["resources"]) != original_count:
            self._save_resources(state)

    def remove(self, *, kind: str, id: str) -> None:
        state = self._load_resources()
        original_count = len(state["resources"])
        state["resources"] = [
            raw for raw in state["resources"] if not (raw.get("kind") == kind and raw.get("id") == id)
        ]
        if len(state["resources"]) != original_count:
            self._save_resources(state)

    def resolve(self, ref: str | None, *, kind: str | None = None) -> AgentResource:
        state = self._load_resources()
        resources = [self._resource_from_raw(raw) for raw in state["resources"]]
        if not ref:
            matches = [resource for resource in resources if kind is None or resource.kind == kind]
            if not matches:
                raise KeyError(f"No {kind or 'resource'} has been created")
            return max(matches, key=lambda resource: resource.idx)

        if ref.isdigit():
            for resource in resources:
                if resource.idx == int(ref) and (kind is None or resource.kind == kind):
                    return resource
        for resource in resources:
            if resource.id == ref and (kind is None or resource.kind == kind):
                return resource

        label_matches = [
            resource
            for resource in resources
            if resource.label == ref and (kind is None or resource.kind == kind)
        ]
        if label_matches:
            return max(label_matches, key=lambda resource: resource.idx)

        expected = f" {kind}" if kind else ""
        raise KeyError(f"Unknown{expected} reference: {ref}")

    def list_resources(self, *, kind: str | None = None) -> list[AgentResource]:
        state = self._load_resources()
        resources = [self._resource_from_raw(raw) for raw in state["resources"]]
        if kind:
            return [resource for resource in resources if resource.kind == kind]
        return resources

    def _load_resources(self) -> dict[str, Any]:
        if not RESOURCES_FILE.exists():
            return {"next_idx": 1, "resources": []}
        try:
            data = json.loads(RESOURCES_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"next_idx": 1, "resources": []}
        data.setdefault("next_idx", 1)
        data.setdefault("resources", [])
        return data

    def _save_resources(self, state: dict[str, Any]) -> None:
        self._atomic_write_json(RESOURCES_FILE, state)

    def _clear_runtime_resources(self) -> None:
        state = self._load_resources()
        original_count = len(state["resources"])
        state["resources"] = [
            raw for raw in state["resources"] if raw.get("kind") not in RUNTIME_RESOURCE_KINDS
        ]
        if len(state["resources"]) != original_count:
            self._save_resources(state)

    def _has_runtime_state(self) -> bool:
        if SESSIONS_DIR.exists() and any(SESSIONS_DIR.glob("*.json")):
            return True
        if not RESOURCES_FILE.exists():
            return False
        return any(raw.get("kind") in RUNTIME_RESOURCE_KINDS for raw in self._load_resources()["resources"])

    def _resource_from_raw(self, raw: dict[str, Any]) -> AgentResource:
        return AgentResource(
            idx=int(raw["idx"]),
            kind=str(raw["kind"]),
            id=str(raw["id"]),
            profile_id=raw.get("profile_id"),
            parent_id=raw.get("parent_id"),
            label=str(raw.get("label") or ""),
            created_at=float(raw.get("created_at") or 0.0),
            runtime_id=raw.get("runtime_id"),
        )

    def _atomic_write_json(self, path: Path, payload: dict[str, Any], *, mode: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        # Create with restrictive perms before writing content
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode or 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        tmp_path.replace(path)
        if mode is not None:
            with suppress(OSError):
                os.chmod(path, mode)
