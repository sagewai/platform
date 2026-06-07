# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
import pytest

from sagewai.core.state import StepStatus, WorkflowRun, WorkflowStore
from sagewai.db import factory


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SAGEWAI_DATABASE_URL", raising=False)
    factory.reset_engine()
    yield
    factory.reset_engine()


def _run(name="wf", run_id="r1", status=StepStatus.PENDING):
    r = WorkflowRun(workflow_name=name, run_id=run_id)
    r.status = status
    return r


@pytest.mark.asyncio
async def test_is_workflow_store():
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore
    s = SqliteWorkflowStore()
    assert isinstance(s, WorkflowStore)
    assert not hasattr(s, "_pool")  # revocation registry must degrade to None


@pytest.mark.asyncio
async def test_save_and_load_roundtrip():
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore
    s = SqliteWorkflowStore()
    await s.initialize()
    run = _run()
    await s.save_run(run)
    loaded = await s.load_run("wf", "r1")
    assert loaded is not None
    assert loaded.workflow_name == "wf" and loaded.run_id == "r1"


@pytest.mark.asyncio
async def test_persists_across_engine_restart():
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore
    s1 = SqliteWorkflowStore()
    await s1.initialize()
    await s1.save_run(_run(run_id="durable"))
    # simulate restart: dispose engine, reopen from same SAGEWAI_HOME
    await factory.dispose_engine()
    factory.reset_engine()
    s2 = SqliteWorkflowStore()
    loaded = await s2.load_run("wf", "durable")
    assert loaded is not None and loaded.run_id == "durable"


@pytest.mark.asyncio
async def test_list_runs_filters_by_status():
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore
    s = SqliteWorkflowStore()
    await s.initialize()
    await s.save_run(_run(run_id="a", status=StepStatus.PENDING))
    await s.save_run(_run(run_id="b", status=StepStatus.RUNNING))
    all_runs = await s.list_runs("wf")
    running = await s.list_runs("wf", status=StepStatus.RUNNING)
    assert len(all_runs) == 2
    assert len(running) == 1 and running[0].run_id == "b"


@pytest.mark.asyncio
async def test_recover_stale_runs_and_heartbeat():
    import asyncio
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore
    s = SqliteWorkflowStore()
    await s.initialize()
    run = _run(run_id="stale", status=StepStatus.RUNNING)
    await s.save_run(run)
    # with a 0s timeout, the running run is immediately stale
    stale = await s.recover_stale_runs(stale_timeout_seconds=0)
    assert any(r.run_id == "stale" for r in stale)
    # heartbeat then a large timeout -> not stale
    await s.heartbeat("wf", "stale")
    fresh = await s.recover_stale_runs(stale_timeout_seconds=3600)
    assert fresh == []


@pytest.mark.asyncio
async def test_project_scoped_no_collision(tmp_path, monkeypatch):
    """Two projects sharing the same workflow_name+run_id must not collide."""
    from sagewai.core.context import ProjectContext
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore

    s = SqliteWorkflowStore()
    await s.initialize()

    # Build two runs with identical workflow_name and run_id but different projects.
    a = WorkflowRun(workflow_name="wf", run_id="same")
    a.project_id = "proj-a"
    a.status = StepStatus.RUNNING

    b = WorkflowRun(workflow_name="wf", run_id="same")
    b.project_id = "proj-b"
    b.status = StepStatus.PENDING

    # save_run uses run.project_id via resolve_project_id — no context needed.
    await s.save_run(a)
    await s.save_run(b)

    # Under proj-a context, load/list see only proj-a's run.
    with ProjectContext(project_id="proj-a"):
        la = await s.load_run("wf", "same")
        assert la is not None and la.status == StepStatus.RUNNING
        assert len(await s.list_runs("wf")) == 1

    # Under proj-b context, load/list see only proj-b's run.
    with ProjectContext(project_id="proj-b"):
        lb = await s.load_run("wf", "same")
        assert lb is not None and lb.status == StepStatus.PENDING
        assert len(await s.list_runs("wf")) == 1


@pytest.mark.asyncio
async def test_default_project_roundtrip():
    """Run saved with no project context loads correctly under the same context."""
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore

    s = SqliteWorkflowStore()
    await s.initialize()

    run = _run(run_id="no-project")
    await s.save_run(run)  # saved under "default" project

    loaded = await s.load_run("wf", "no-project")
    assert loaded is not None and loaded.run_id == "no-project"

    all_runs = await s.list_runs("wf")
    assert any(r.run_id == "no-project" for r in all_runs)
