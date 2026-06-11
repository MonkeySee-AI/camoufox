from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROTUNDA_EXECUTABLE_PATH = "ROTUNDA_EXECUTABLE_PATH"
ROTUNDA_CONFIG_PATH = "ROTUNDA_CONFIG_PATH"
ROTUNDA_MACOS_BACKGROUND_WINDOWS = "ROTUNDA_MACOS_BACKGROUND_WINDOWS"

ROTUNDA_DEBUG_DUMP = "ROTUNDA_DEBUG_DUMP"
ROTUNDA_DEBUG_DUMP_DIR = "ROTUNDA_DEBUG_DUMP_DIR"
ROTUNDA_DEBUG_DUMP_MAX_BODY = "ROTUNDA_DEBUG_DUMP_MAX_BODY"
ROTUNDA_DEBUG_DUMP_RAW = "ROTUNDA_DEBUG_DUMP_RAW"

ROTUNDA_VM_ACCESS_LOG = "ROTUNDA_VM_ACCESS_LOG"
ROTUNDA_VM_ACCESS_LOG_FILE = "ROTUNDA_VM_ACCESS_LOG_FILE"
ROTUNDA_VM_ACCESS_BUFFERED = "ROTUNDA_VM_ACCESS_BUFFERED"
ROTUNDA_VM_ACCESS_REALM = "ROTUNDA_VM_ACCESS_REALM"
ROTUNDA_VM_ACCESS_SYMBOLS = "ROTUNDA_VM_ACCESS_SYMBOLS"
ROTUNDA_VM_ACCESS_RETURNS = "ROTUNDA_VM_ACCESS_RETURNS"
ROTUNDA_VM_ACCESS_VALUE_STRINGS = "ROTUNDA_VM_ACCESS_VALUE_STRINGS"
ROTUNDA_VM_ACCESS_FUNCTION_NAMES = "ROTUNDA_VM_ACCESS_FUNCTION_NAMES"
ROTUNDA_VM_ACCESS_FILTER = "ROTUNDA_VM_ACCESS_FILTER"
ROTUNDA_VM_ACCESS_OBJECT_FILTER = "ROTUNDA_VM_ACCESS_OBJECT_FILTER"
ROTUNDA_VM_ACCESS_MAX_ARGS = "ROTUNDA_VM_ACCESS_MAX_ARGS"
ROTUNDA_VM_ACCESS_MAX_STRING = "ROTUNDA_VM_ACCESS_MAX_STRING"
ROTUNDA_VM_ACCESS_MAX_QUEUE_BYTES = "ROTUNDA_VM_ACCESS_MAX_QUEUE_BYTES"
ROTUNDA_VM_ACCESS_SAMPLE_RATE = "ROTUNDA_VM_ACCESS_SAMPLE_RATE"

DEBUG_DUMP_ENV_VAR_NAMES = (
    ROTUNDA_DEBUG_DUMP,
    ROTUNDA_DEBUG_DUMP_DIR,
    ROTUNDA_DEBUG_DUMP_MAX_BODY,
    ROTUNDA_DEBUG_DUMP_RAW,
)
VM_ACCESS_ENV_VAR_NAMES = (
    ROTUNDA_VM_ACCESS_LOG,
    ROTUNDA_VM_ACCESS_LOG_FILE,
    ROTUNDA_VM_ACCESS_BUFFERED,
    ROTUNDA_VM_ACCESS_REALM,
    ROTUNDA_VM_ACCESS_SYMBOLS,
    ROTUNDA_VM_ACCESS_RETURNS,
    ROTUNDA_VM_ACCESS_VALUE_STRINGS,
    ROTUNDA_VM_ACCESS_FUNCTION_NAMES,
    ROTUNDA_VM_ACCESS_FILTER,
    ROTUNDA_VM_ACCESS_OBJECT_FILTER,
    ROTUNDA_VM_ACCESS_MAX_ARGS,
    ROTUNDA_VM_ACCESS_MAX_STRING,
    ROTUNDA_VM_ACCESS_MAX_QUEUE_BYTES,
    ROTUNDA_VM_ACCESS_SAMPLE_RATE,
)
SUPPORTED_ENV_VAR_NAMES = (
    ROTUNDA_EXECUTABLE_PATH,
    ROTUNDA_MACOS_BACKGROUND_WINDOWS,
    *DEBUG_DUMP_ENV_VAR_NAMES,
    *VM_ACCESS_ENV_VAR_NAMES,
)
LAUNCH_MANIFEST_ENV_VAR_NAMES = (
    ROTUNDA_CONFIG_PATH,
    ROTUNDA_MACOS_BACKGROUND_WINDOWS,
    *DEBUG_DUMP_ENV_VAR_NAMES,
    *VM_ACCESS_ENV_VAR_NAMES,
)


class RotundaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
    )

    executable_path: str | None = Field(
        default=None,
        validation_alias=ROTUNDA_EXECUTABLE_PATH,
    )
    macos_background_windows: bool | None = Field(
        default=None,
        validation_alias=ROTUNDA_MACOS_BACKGROUND_WINDOWS,
    )
    debug_dump: str | None = Field(
        default=None,
        validation_alias=ROTUNDA_DEBUG_DUMP,
    )
    debug_dump_dir: str | None = Field(
        default=None,
        validation_alias=ROTUNDA_DEBUG_DUMP_DIR,
    )
    debug_dump_max_body: int = Field(
        default=1_048_576,
        validation_alias=ROTUNDA_DEBUG_DUMP_MAX_BODY,
    )
    debug_dump_raw: bool = Field(
        default=False,
        validation_alias=ROTUNDA_DEBUG_DUMP_RAW,
    )

    vm_access_log: bool | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_LOG,
    )
    vm_access_log_file: str | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_LOG_FILE,
    )
    vm_access_buffered: bool | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_BUFFERED,
    )
    vm_access_realm: bool | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_REALM,
    )
    vm_access_symbols: bool | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_SYMBOLS,
    )
    vm_access_returns: bool | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_RETURNS,
    )
    vm_access_value_strings: bool | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_VALUE_STRINGS,
    )
    vm_access_function_names: bool | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_FUNCTION_NAMES,
    )
    vm_access_filter: str | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_FILTER,
    )
    vm_access_object_filter: str | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_OBJECT_FILTER,
    )
    vm_access_max_args: int | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_MAX_ARGS,
    )
    vm_access_max_string: int | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_MAX_STRING,
    )
    vm_access_max_queue_bytes: int | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_MAX_QUEUE_BYTES,
    )
    vm_access_sample_rate: int | None = Field(
        default=None,
        validation_alias=ROTUNDA_VM_ACCESS_SAMPLE_RATE,
    )

    @field_validator("debug_dump_max_body", mode="before")
    @classmethod
    def _coerce_debug_dump_max_body(cls, value: Any) -> int:
        try:
            parsed = int(str(value))
        except Exception:
            return 1_048_576
        return max(0, parsed)

    @field_validator("debug_dump_raw", mode="before")
    @classmethod
    def _coerce_debug_dump_raw(cls, value: Any) -> bool:
        return _env_flag(value)

    @field_validator(
        "vm_access_log",
        "macos_background_windows",
        "vm_access_buffered",
        "vm_access_realm",
        "vm_access_symbols",
        "vm_access_returns",
        "vm_access_value_strings",
        "vm_access_function_names",
        mode="before",
    )
    @classmethod
    def _coerce_optional_flag(cls, value: Any) -> bool | None:
        if value is None:
            return None
        return _env_flag(value)

    @field_validator(
        "vm_access_max_args",
        "vm_access_max_string",
        "vm_access_max_queue_bytes",
        "vm_access_sample_rate",
        mode="before",
    )
    @classmethod
    def _coerce_optional_int(cls, value: Any) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(str(value))
        except Exception:
            return None
        return max(0, parsed)

    @classmethod
    def from_env(cls, env: Mapping[str, Any] | None = None) -> RotundaSettings:
        if env is None:
            return cls()
        return cls.model_validate(
            {name: env[name] for name in SUPPORTED_ENV_VAR_NAMES if name in env}
        )


def env_snapshot(
    env: Mapping[str, Any],
    names: tuple[str, ...] = LAUNCH_MANIFEST_ENV_VAR_NAMES,
) -> dict[str, Any]:
    return {name: env.get(name) for name in names}


def _env_flag(value: Any) -> bool:
    if value is None:
        return False
    return str(value).lower() not in {"", "0", "false", "no"}
