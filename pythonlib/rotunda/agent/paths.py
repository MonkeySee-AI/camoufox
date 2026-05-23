from __future__ import annotations

import os
from pathlib import Path

AGENT_HOME = Path.home() / ".rotunda" / "agent"
PROFILES_DIR = AGENT_HOME / "profiles"
SESSIONS_DIR = AGENT_HOME / "sessions"
LOGS_DIR = AGENT_HOME / "logs"
AUTH_FILE = AGENT_HOME / "auth.json"
DAEMON_FILE = AGENT_HOME / "daemon.json"
LOCK_FILE = AGENT_HOME / "startup.lock"
RESOURCES_FILE = AGENT_HOME / "resources.json"

# Permissions for sensitive directories and files (owner read/write only)
_DIR_MODE = 0o700
_FILE_MODE = 0o600


def _secure_mkdir(path: Path) -> None:
    """Create a directory with restrictive permissions (owner-only)."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, _DIR_MODE)
    except OSError:
        pass


def ensure_agent_dirs() -> None:
    _secure_mkdir(AGENT_HOME)
    _secure_mkdir(PROFILES_DIR)
    _secure_mkdir(SESSIONS_DIR)
    _secure_mkdir(LOGS_DIR)
