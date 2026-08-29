# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Explicit host command runner used only by deterministic unit tests."""

from __future__ import annotations

from sagewai.fleet.execution import WorkerProcessResult, run_worker_subprocess
from sagewai.work.profiles.software.models import SoftwareWorkspace


class LocalVerificationRunner:
    """Preserve legacy unit-test command behavior without a production fallback."""

    async def run(
        self,
        *,
        project_id: str,
        work_id: str,
        attempt_id: str,
        workspace: SoftwareWorkspace,
        commands: tuple[tuple[str, ...], ...],
        timeout: float,
    ) -> tuple[WorkerProcessResult, ...]:
        del project_id, work_id, attempt_id
        results = []
        for argv in commands:
            results.append(
                await run_worker_subprocess(
                    argv=argv,
                    cwd=workspace.path,
                    timeout=timeout,
                    output_limit=None,
                )
            )
        return tuple(results)
