# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""run_worker_subprocess streams stdout and stderr lines while keeping the buffered result."""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from sagewai.fleet.execution import run_worker_subprocess

PY = sys.executable


async def assert_pid_gone(pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"pid {pid} is still alive")


@pytest.mark.asyncio
async def test_line_callbacks_receive_each_stream_in_order() -> None:
    out: list[str] = []
    err: list[str] = []
    result = await run_worker_subprocess(
        argv=[
            PY,
            "-c",
            "import sys\nfor i in range(3):\n    print(f'o{i}', flush=True)\n    print(f'e{i}', file=sys.stderr, flush=True)",
        ],
        on_stdout_line=out.append,
        on_stderr_line=err.append,
    )
    assert result.returncode == 0
    assert out == ["o0", "o1", "o2"]
    assert err == ["e0", "e1", "e2"]
    assert result.stdout == "o0\no1\no2\n"


@pytest.mark.asyncio
async def test_callback_failure_is_logged_and_never_fails_the_run(caplog: pytest.LogCaptureFixture) -> None:
    def boom(line: str) -> None:
        raise RuntimeError("sink down")

    result = await run_worker_subprocess(argv=[PY, "-c", "print('x')"], on_stdout_line=boom)
    assert result.returncode == 0
    assert result.stdout == "x\n"
    assert "sink down" in caplog.text


@pytest.mark.asyncio
async def test_timeout_keeps_partial_output_and_kills_the_process_group() -> None:
    lines: list[str] = []
    result = await run_worker_subprocess(
        argv=[PY, "-c", "import time,sys\nprint('partial', flush=True)\ntime.sleep(30)"],
        timeout=1.5,
        on_stdout_line=lines.append,
    )
    assert result.timed_out
    assert result.returncode != 0
    assert lines == ["partial"]
    assert result.stdout == "partial\n"


@pytest.mark.asyncio
async def test_output_limit_bounds_the_buffered_result_but_not_the_callbacks() -> None:
    lines: list[str] = []
    result = await run_worker_subprocess(
        argv=[PY, "-c", "print('a' * 100)\nprint('b' * 100)"],
        output_limit=120,
        on_stdout_line=lines.append,
    )
    assert [len(line) for line in lines] == [100, 100]
    assert len(result.stdout) <= 120
    assert result.stdout.endswith("b" * 100 + "\n")


@pytest.mark.asyncio
async def test_cancellation_kills_the_process() -> None:
    pid_ready = asyncio.Event()
    pids: list[int] = []

    def capture_pid(line: str) -> None:
        pids.append(int(line))
        pid_ready.set()

    task = asyncio.create_task(
        run_worker_subprocess(
            argv=[PY, "-c", "import os,time\nprint(os.getpid(), flush=True)\ntime.sleep(30)"],
            on_stdout_line=capture_pid,
        )
    )
    await asyncio.wait_for(pid_ready.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await assert_pid_gone(pids[0])


@pytest.mark.asyncio
async def test_one_long_stdout_line_streams_as_one_line_and_is_buffered_exactly() -> None:
    lines: list[str] = []
    result = await run_worker_subprocess(
        argv=[PY, "-c", "print('x' * 300000)"],
        output_limit=None,
        on_stdout_line=lines.append,
    )
    assert result.returncode == 0
    assert [len(line) for line in lines] == [300000]
    assert result.stdout == "x" * 300000 + "\n"


@pytest.mark.asyncio
async def test_large_stdin_to_immediate_exit_returns_normally() -> None:
    result = await run_worker_subprocess(argv=[PY, "-c", "pass"], stdin="x" * (4 * 1024 * 1024))
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_large_stdin_to_closed_stdin_does_not_surface_broken_pipe() -> None:
    result = await run_worker_subprocess(
        argv=[PY, "-c", "import os,time\nos.close(0)\ntime.sleep(0.2)"],
        stdin="x" * (4 * 1024 * 1024),
    )
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_timeout_kills_grandchild_process_group_and_keeps_partial_output() -> None:
    lines: list[str] = []
    result = await run_worker_subprocess(
        argv=[
            PY,
            "-c",
            "import os,subprocess,sys,time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "print(os.getpid(), child.pid, flush=True)\n"
            "time.sleep(30)",
        ],
        timeout=1.5,
        on_stdout_line=lines.append,
    )
    assert result.timed_out
    assert lines
    pids = [int(pid) for pid in lines[0].split()]
    assert result.stdout == lines[0] + "\n"
    for pid in pids:
        await assert_pid_gone(pid)


@pytest.mark.asyncio
async def test_eof_without_exit_is_bounded_by_timeout() -> None:
    started_at = time.monotonic()
    result = await run_worker_subprocess(
        argv=[PY, "-c", "import os,time\nos.close(1)\nos.close(2)\ntime.sleep(30)"],
        timeout=1.0,
    )
    assert time.monotonic() - started_at < 5
    assert result.timed_out
    assert result.returncode == -9
