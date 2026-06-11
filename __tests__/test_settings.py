from __future__ import annotations

from rotunda.settings import (
    ROTUNDA_CONFIG_PATH,
    ROTUNDA_DEBUG_DUMP,
    ROTUNDA_DEBUG_DUMP_DIR,
    ROTUNDA_DEBUG_DUMP_MAX_BODY,
    ROTUNDA_DEBUG_DUMP_RAW,
    ROTUNDA_EXECUTABLE_PATH,
    ROTUNDA_VM_ACCESS_FUNCTION_NAMES,
    ROTUNDA_VM_ACCESS_LOG,
    ROTUNDA_VM_ACCESS_LOG_FILE,
    ROTUNDA_VM_ACCESS_MAX_ARGS,
    ROTUNDA_VM_ACCESS_MAX_QUEUE_BYTES,
    ROTUNDA_VM_ACCESS_MAX_STRING,
    ROTUNDA_VM_ACCESS_SAMPLE_RATE,
    ROTUNDA_VM_ACCESS_SYMBOLS,
    RotundaSettings,
    env_snapshot,
)


def test_settings_parses_supported_rotunda_env_overrides() -> None:
    settings = RotundaSettings.from_env(
        {
            ROTUNDA_EXECUTABLE_PATH: "/tmp/rotunda-bin",
            ROTUNDA_DEBUG_DUMP: "manifest,vm",
            ROTUNDA_DEBUG_DUMP_DIR: "/tmp/rotunda-debug",
            ROTUNDA_DEBUG_DUMP_MAX_BODY: "1024",
            ROTUNDA_DEBUG_DUMP_RAW: "yes",
            ROTUNDA_VM_ACCESS_LOG: "1",
            ROTUNDA_VM_ACCESS_LOG_FILE: "/tmp/vm.log",
            ROTUNDA_VM_ACCESS_SYMBOLS: "0",
            ROTUNDA_VM_ACCESS_FUNCTION_NAMES: "false",
            ROTUNDA_VM_ACCESS_MAX_ARGS: "8",
            ROTUNDA_VM_ACCESS_MAX_STRING: "256",
            ROTUNDA_VM_ACCESS_MAX_QUEUE_BYTES: "67108864",
            ROTUNDA_VM_ACCESS_SAMPLE_RATE: "10",
            "GITHUB_TOKEN": "not-a-rotunda-runtime-setting",
        }
    )

    assert settings.executable_path == "/tmp/rotunda-bin"
    assert settings.debug_dump == "manifest,vm"
    assert settings.debug_dump_dir == "/tmp/rotunda-debug"
    assert settings.debug_dump_max_body == 1024
    assert settings.debug_dump_raw is True
    assert settings.vm_access_log is True
    assert settings.vm_access_log_file == "/tmp/vm.log"
    assert settings.vm_access_symbols is False
    assert settings.vm_access_function_names is False
    assert settings.vm_access_max_args == 8
    assert settings.vm_access_max_string == 256
    assert settings.vm_access_max_queue_bytes == 67_108_864
    assert settings.vm_access_sample_rate == 10


def test_settings_uses_runtime_compatible_fallbacks_for_bad_env_values() -> None:
    settings = RotundaSettings.from_env(
        {
            ROTUNDA_DEBUG_DUMP_MAX_BODY: "invalid",
            ROTUNDA_VM_ACCESS_MAX_ARGS: "invalid",
        }
    )

    assert settings.debug_dump_max_body == 1_048_576
    assert settings.vm_access_max_args is None


def test_env_snapshot_uses_central_rotunda_env_name_list() -> None:
    env = {
        ROTUNDA_CONFIG_PATH: "/tmp/profile.json",
        ROTUNDA_DEBUG_DUMP_DIR: "/tmp/debug",
    }

    snapshot = env_snapshot(env)

    assert snapshot[ROTUNDA_CONFIG_PATH] == "/tmp/profile.json"
    assert snapshot[ROTUNDA_DEBUG_DUMP_DIR] == "/tmp/debug"
    assert ROTUNDA_VM_ACCESS_LOG in snapshot
