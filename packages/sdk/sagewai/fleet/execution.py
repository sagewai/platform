# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Default-deny subprocess execution shared by Fleet and native operators."""

from __future__ import annotations

import asyncio
import codecs
import logging
import os
import signal
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

EXEC_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TMPDIR",
        "PWD",
    }
)

LineCallback = Callable[[str], None]


class WorkerConfigurationError(RuntimeError):
    """A trusted worker-local configuration check failed before task execution."""


@dataclass(frozen=True)
class WorkerProcessResult:
    """Bounded receipt from one worker-side subprocess."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


async def run_worker_subprocess(
    *,
    argv: Sequence[str] | None = None,
    command: str | None = None,
    stdin: str = "",
    explicit_env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float | None = None,
    output_limit: int | None = 4000,
    on_stdout_line: LineCallback | None = None,
    on_stderr_line: LineCallback | None = None,
) -> WorkerProcessResult:
    """Run a worker process with the allowlisted environment, streaming lines as they arrive.

    ``argv`` takes precedence over ``command``, which runs through ``/bin/sh -c``.
    Callbacks run per decoded line (without the newline); a callback exception is logged
    and never fails the run. A timeout kills the whole process group and returns the
    partial output with ``timed_out=True``. Cancellation kills the process group too.
    """
    env = _build_env(explicit_env)
    if argv is None:
        if command is None:
            raise ValueError("argv or command is required")
        argv = ["/bin/sh", "-c", command]
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    async def _pump(stream: asyncio.StreamReader, chunks: list[str], callback: LineCallback | None) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending_line = ""
        while True:
            raw = await stream.read(65536)
            if not raw:
                tail = decoder.decode(b"", final=True)
                if tail:
                    chunks.append(tail)
                    pending_line += tail
                if pending_line:
                    _call_line_callback(callback, pending_line.rstrip("\r"))
                return
            text = decoder.decode(raw)
            chunks.append(text)
            pending_line += text
            while "\n" in pending_line:
                line, pending_line = pending_line.split("\n", 1)
                _call_line_callback(callback, line.rstrip("\r"))

    async def _feed_stdin() -> None:
        assert process.stdin is not None
        try:
            if stdin:
                process.stdin.write(stdin.encode("utf-8"))
                await process.stdin.drain()
            process.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

    stdout_stream = process.stdout
    stderr_stream = process.stderr
    assert stdout_stream is not None and stderr_stream is not None

    async def _drain() -> None:
        await asyncio.gather(
            _feed_stdin(),
            _pump(stdout_stream, stdout_chunks, on_stdout_line),
            _pump(stderr_stream, stderr_chunks, on_stderr_line),
        )
        await process.wait()

    timed_out = False
    try:
        await asyncio.wait_for(_drain(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        _kill_group(process)
        await process.wait()
    except asyncio.CancelledError:
        _kill_group(process)
        await process.wait()
        raise
    return WorkerProcessResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=_bounded("".join(stdout_chunks), output_limit),
        stderr=_bounded("".join(stderr_chunks), output_limit),
        timed_out=timed_out,
    )


def _build_env(explicit_env: Mapping[str, str] | None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in EXEC_ENV_ALLOWLIST}
    env.update(explicit_env or {})
    return env


def _call_line_callback(callback: LineCallback | None, line: str) -> None:
    if callback is None:
        return
    try:
        callback(line)
    except Exception:
        logger.exception("line callback failed")


def _kill_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _bounded(text: str, limit: int | None) -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[-limit:]
