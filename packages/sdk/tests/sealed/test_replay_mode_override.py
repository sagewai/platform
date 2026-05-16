# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Sealed-iii.C extensions — execution_mode_override + identity_from."""
from __future__ import annotations

import pytest

from sagewai.core.state import ExecutionMode, WorkflowRun
from sagewai.sealed.replay import enqueue_replay


class _FakeStore:
    def __init__(self) -> None:
        self.saved: list[WorkflowRun] = []
        self.original = WorkflowRun(
            workflow_name="wf",
            run_id="r-orig",
            execution_mode=ExecutionMode.SANDBOXED,
            security_profile_ref=None,
        )

    async def load_run(self, run_id: str) -> WorkflowRun:
        return self.original

    async def save_run(self, run: WorkflowRun) -> None:
        self.saved.append(run)


@pytest.mark.asyncio
async def test_replay_with_execution_mode_override_records_field():
    store = _FakeStore()
    new_run = await enqueue_replay(
        store=store,
        original_run_id="r-orig",
        from_step=2,
        execution_mode_override=ExecutionMode.IDENTITY,
        identity_from="current_cascade",
        security_profile_ref="customer-db",
    )
    assert new_run.execution_mode_override is ExecutionMode.IDENTITY
    assert new_run.identity_from == "current_cascade"
    assert new_run.security_profile_ref == "customer-db"
    assert new_run.replay_of_run_id == "r-orig"
    assert new_run.replay_from_step == 2


@pytest.mark.asyncio
async def test_replay_without_overrides_preserves_original_mode():
    store = _FakeStore()
    new_run = await enqueue_replay(
        store=store,
        original_run_id="r-orig",
        from_step=0,
    )
    assert new_run.execution_mode_override is None
    assert new_run.identity_from in (None, "original_injection")
    # New run picks up original's mode unless override.
    assert new_run.execution_mode is ExecutionMode.SANDBOXED
