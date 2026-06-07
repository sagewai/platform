# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end restart-durability guarantee.

Proves that with a default install (no SAGEWAI_DATABASE_URL), all
runtime state + vector learnings persist across a simulated process
restart in SQLite.

Covers four durable subsystems:
  1. Agent runs  (RunStore / agent_runs table)
  2. Analytics   (PostgresAnalyticsStore / cost_records table)
  3. Workflow checkpoints (SqliteWorkflowStore / workflow_runs table)
  4. Vector learnings    (SqliteVecMemory / vec0 virtual table)

NOT marked @pytest.mark.integration — SQLite-only, runs in the normal suite
with no external services required.
"""
from __future__ import annotations

import pytest

from sagewai.db import factory


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Isolate each test to a fresh temporary SAGEWAI_HOME."""
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SAGEWAI_DATABASE_URL", raising=False)
    factory.reset_engine()
    yield
    factory.reset_engine()


@pytest.mark.asyncio
async def test_default_backend_is_sqlite_in_home():
    """resolve_database_url() must point at the SQLite file inside SAGEWAI_HOME."""
    from sagewai import home

    url = factory.resolve_database_url()
    assert url.startswith("sqlite+aiosqlite:///"), (
        f"Expected sqlite+aiosqlite:/// scheme, got: {url!r}"
    )
    expected_path = str(home.db_dir() / "sagewai.db")
    assert expected_path in url, (
        f"Expected path {expected_path!r} not found in URL {url!r}"
    )


@pytest.mark.asyncio
async def test_all_state_survives_restart():
    """All four durable subsystems retain data across a simulated process restart.

    The restart is simulated by:
      1. Writing state to every subsystem.
      2. Calling dispose_engine() + reset_engine() to release all connections
         and clear the process-level engine cache.
      3. Calling get_engine() again — which opens a brand-new connection to
         the same on-disk SQLite file.
      4. Reading back from every subsystem and asserting all state is intact.
    """
    from sagewai import home
    from sagewai.admin.postgres_analytics import PostgresAnalyticsStore
    from sagewai.admin.store import RunStore
    from sagewai.core.state import StepStatus, WorkflowRun
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore
    from sagewai.intelligence.embeddings.hash_embedder import HashEmbedder
    from sagewai.memory.sqlite_vec import SqliteVecMemory

    # ---- bootstrap schema ----
    await factory.ensure_schema()
    eng = factory.get_engine()

    # All stores share the same db file; resolve it once for SqliteVecMemory.
    db_path = str(home.db_dir() / "sagewai.db")

    # ================================================================
    # WRITE — populate every durable subsystem
    # ================================================================

    # 1. Agent run
    run_store = RunStore(engine=eng)
    await run_store.init()
    rid = await run_store.save_run(
        agent_name="scout",
        input_text="hi",
        project_id="p1",
    )
    assert rid, "save_run must return a non-empty run_id"

    # 2. Analytics cost record
    analytics = PostgresAnalyticsStore(engine=eng)
    await analytics.record_cost("scout", "m", 2.5, 100, "p1")

    # 3. Workflow checkpoint
    wf_store = SqliteWorkflowStore(engine=eng)
    await wf_store.initialize()
    run = WorkflowRun(workflow_name="pipeline", run_id="r-durable")
    run.status = StepStatus.RUNNING
    await wf_store.save_run(run)

    # 4. Vector learning (sqlite-vec uses a raw sqlite3 connection to the same file)
    emb = HashEmbedder(dimension=384)
    mem = SqliteVecMemory(embedder=emb, project_id="p1", db_path=db_path)
    await mem.store("learned: prefer SQLite for local installs")
    await mem.close()

    # ================================================================
    # SIMULATE RESTART — dispose engine + reset cache, then reopen
    # ================================================================
    await factory.dispose_engine()
    factory.reset_engine()
    eng2 = factory.get_engine()

    # ================================================================
    # READ — assert every subsystem still has its state
    # ================================================================

    # 1. Agent run persisted
    runs = await RunStore(engine=eng2).list_runs(project_id="p1")
    assert any(r.run_id == rid for r in runs), (
        f"Agent run {rid!r} lost across restart; found: {[r.run_id for r in runs]}"
    )

    # 2. Analytics cost persisted
    costs = await PostgresAnalyticsStore(engine=eng2).get_costs(project_id="p1")
    assert costs["total_cost_usd"] >= 2.5, (
        f"Analytics cost lost across restart; got: {costs}"
    )

    # 3. Workflow checkpoint persisted
    loaded = await SqliteWorkflowStore(engine=eng2).load_run("pipeline", "r-durable")
    assert loaded is not None, "Workflow checkpoint lost across restart"
    assert loaded.run_id == "r-durable", (
        f"Loaded wrong run; expected 'r-durable', got {loaded.run_id!r}"
    )

    # 4. Vector learning persisted (sqlite-vec reopens its own connection to same file)
    mem2 = SqliteVecMemory(
        embedder=HashEmbedder(dimension=384),
        project_id="p1",
        db_path=db_path,
    )
    learnings = await mem2.retrieve("SQLite local", top_k=5)
    assert any("SQLite" in r for r in learnings), (
        f"Vector learning lost across restart; retrieved: {learnings!r}"
    )
    await mem2.close()
