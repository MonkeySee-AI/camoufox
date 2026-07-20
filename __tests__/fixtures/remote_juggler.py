# Copyright (c) 2026 Pierce Freeman.

import asyncio
import os
import re
from pathlib import Path

_JUGGLER_ENDPOINT_RE = re.compile(r"Juggler listening on (ws://\S+)")


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=5)


async def _collect_output(
    stream: asyncio.StreamReader | None,
    label: str,
    endpoint_future: asyncio.Future[str],
    logs: list[str],
) -> None:
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode(errors="replace").rstrip()
        logs.append(f"{label}: {text}")
        match = _JUGGLER_ENDPOINT_RE.search(text)
        if match and not endpoint_future.done():
            endpoint_future.set_result(match.group(1))


async def launch_remote_juggler(
    executable_path: str,
    profile_dir: Path,
) -> tuple[asyncio.subprocess.Process, str, list[str], list[asyncio.Task[None]]]:
    env = os.environ.copy()
    env.pop("ROTUNDA_CONFIG_PATH", None)

    process = await asyncio.create_subprocess_exec(
        executable_path,
        "--headless",
        "--profile",
        str(profile_dir),
        "--juggler-port",
        "0",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    logs: list[str] = []
    endpoint_future = asyncio.get_running_loop().create_future()
    readers = [
        asyncio.create_task(
            _collect_output(process.stdout, "stdout", endpoint_future, logs)
        ),
        asyncio.create_task(
            _collect_output(process.stderr, "stderr", endpoint_future, logs)
        ),
    ]
    process_wait = asyncio.create_task(process.wait())

    try:
        done, _ = await asyncio.wait(
            {endpoint_future, process_wait},
            timeout=30,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if endpoint_future in done:
            return process, endpoint_future.result(), logs, readers
        if process_wait in done:
            raise AssertionError(
                "Rotunda exited before reporting a Juggler endpoint.\n"
                + "\n".join(logs[-50:])
            )
        raise AssertionError(
            "Timed out waiting for Rotunda to report a Juggler endpoint.\n"
            + "\n".join(logs[-50:])
        )
    except Exception:
        await terminate_process(process)
        raise
    finally:
        process_wait.cancel()
