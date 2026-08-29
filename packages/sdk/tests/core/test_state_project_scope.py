# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Project-isolation tests for the in-memory workflow store."""

from __future__ import annotations

import pytest

from sagewai.core import state
from sagewai.core.state import InMemoryStore, StepStatus, WorkflowRun


@pytest.mark.asyncio
async def test_identical_workflow_run_ids_are_isolated_by_explicit_project_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(state.time, "time", lambda: now)
    store = InMemoryStore()
    workflow_name = "work:work-1:implement"
    scopes = ("project-a", "project-b", None, "global")

    for project_id in scopes:
        await store.save_run(
            WorkflowRun(
                workflow_name=workflow_name,
                run_id="run-1",
                project_id=project_id,
                status=StepStatus.RUNNING,
                output_data={"owner": project_id},
            )
        )

    for project_id in scopes:
        loaded = await store.load_run(
            workflow_name,
            "run-1",
            project_id=project_id,
        )
        assert loaded is not None
        assert loaded.project_id == project_id
        assert await store.list_runs(
            workflow_name,
            project_id=project_id,
        ) == [loaded]

    now = 1_000.0
    await store.heartbeat(
        workflow_name,
        "run-1",
        project_id="project-a",
    )

    assert await store.recover_stale_runs(
        project_id="project-a",
        stale_timeout_seconds=300,
    ) == []
    for project_id in ("project-b", None, "global"):
        stale = await store.recover_stale_runs(
            project_id=project_id,
            stale_timeout_seconds=300,
        )
        assert len(stale) == 1
        assert stale[0].project_id == project_id

@pytest.mark.asyncio
async def test_durable_workflow_per_call_scope_preserves_identical_public_ids() -> None:
    store = InMemoryStore()
    workflow = state.DurableWorkflow(name="shared", store=store)

    @workflow.step("echo")
    async def echo(value: str) -> str:
        return value

    await workflow.run(run_id="same", project_id="project-a", value="a")
    await workflow.run(run_id="same", project_id="project-b", value="b")

    project_a = await store.load_run("shared", "same", project_id="project-a")
    project_b = await store.load_run("shared", "same", project_id="project-b")
    assert project_a is not None and project_a.output_data == "a"
    assert project_b is not None and project_b.output_data == "b"
