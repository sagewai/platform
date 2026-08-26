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
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

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
    output_limit: int = 4000,
) -> WorkerProcessResult:
    """Run one command with only system variables plus explicitly scoped env."""
    if (argv is None) == (command is None):
        raise ValueError("exactly one of argv or command is required")

    env = {key: value for key, value in os.environ.items() if key in EXEC_ENV_ALLOWLIST}
    env.update(explicit_env or {})
    kwargs = {
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "env": env,
        "cwd": cwd,
    }
    if argv is not None:
        process = await asyncio.create_subprocess_exec(*argv, **kwargs)
    else:
        process = await asyncio.create_subprocess_shell(command, **kwargs)

    try:
        communication = process.communicate(stdin.encode())
        if timeout is None:
            stdout, stderr = await communication
        else:
            stdout, stderr = await asyncio.wait_for(communication, timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return WorkerProcessResult(
            returncode=process.returncode or -1,
            stdout="",
            stderr="timeout",
            timed_out=True,
        )
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise

    return WorkerProcessResult(
        returncode=process.returncode or 0,
        stdout=stdout.decode(errors="replace")[:output_limit],
        stderr=stderr.decode(errors="replace")[:output_limit],
    )
