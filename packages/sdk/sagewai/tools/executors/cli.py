# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""``kind: cli`` executor — sandbox-aware subprocess invocation."""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from sagewai.tools.registry import CatalogEntry


class CliExecutionError(RuntimeError):
    pass


async def _run_subprocess(argv: list[str]):
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    return type("Result", (), {"stdout": stdout, "stderr": stderr, "returncode": proc.returncode})


async def run(
    entry: CatalogEntry,
    *,
    operation: str | None,
    inputs: dict[str, Any],
    project_id: str,
    get_credentials: Callable[..., Any],
) -> dict[str, Any]:
    cfg = entry.exec_["cli"]
    binary = cfg["binary"]
    argv = [binary] + [arg.format(**inputs) for arg in cfg["argv_template"]]
    result = await _run_subprocess(argv)
    if result.returncode != 0:
        raise CliExecutionError(f"{binary} exited {result.returncode}: {result.stderr!r}")
    return {
        "stdout": result.stdout.decode(),
        "stderr": result.stderr.decode(),
        "returncode": result.returncode,
    }
