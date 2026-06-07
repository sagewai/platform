# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Schema bootstrap tests — validates that Base.metadata.create_all() on SQLite
produces tables that are structurally equivalent to the live Postgres schema
(migrations 001–008). These tests guard against JSONB server-default breakage
and against columns added by migrations but missing from the ORM models.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select

from sagewai.db.engine import create_engine
from sagewai.db.models import Base, WorkflowRunModel


@pytest.mark.asyncio
async def test_create_all_on_sqlite_has_inscope_tables(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    await engine.dispose()
    for t in (
        "agent_runs",
        "workflow_runs",
        "sessions",
        "saved_workflows",
        "saved_workflow_versions",
        "cost_records",
        "guardrail_events",
        "budget_limits",
        "budget_spend",
        "guardrail_configs",
        "sealed_revocations",
        "sealed_audit_events",
        "directive_evaluations",
        "pending_directive_approvals",
    ):
        assert t in tables, f"missing table: {t}"


@pytest.mark.asyncio
async def test_workflow_runs_has_migration_columns(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("workflow_runs")}
        )
    await engine.dispose()
    for col in (
        "execution_mode",
        "artifact_destination",
        "replay_of_run_id",
        "revoked_at",
        "security_profile_ref",
        "idempotency_key",
    ):
        assert col in cols, f"missing workflow_runs column: {col}"


@pytest.mark.asyncio
async def test_sessions_composite_pk(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        pk = await conn.run_sync(
            lambda c: set(
                inspect(c).get_pk_constraint("sessions")["constrained_columns"]
            )
        )
    await engine.dispose()
    assert pk == {"session_id", "project_id"}


@pytest.mark.asyncio
async def test_json_column_roundtrips_on_sqlite(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            WorkflowRunModel.__table__.insert().values(
                id="r1",
                workflow_name="wf",
                run_id="run1",
                status="pending",
                data={"a": [1, 2], "b": {"c": 3}},
            )
        )
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(WorkflowRunModel.__table__.c.data).where(
                    WorkflowRunModel.__table__.c.id == "r1"
                )
            )
        ).scalar_one()
    await engine.dispose()
    assert row == {"a": [1, 2], "b": {"c": 3}}  # JSON round-trips as dict, not string


@pytest.mark.asyncio
async def test_array_columns_roundtrip_list_on_sqlite(tmp_path):
    from sqlalchemy import select
    from sagewai.db.models import WorkflowRunModel
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            WorkflowRunModel.__table__.insert().values(
                id="r1", workflow_name="wf", run_id="run1", status="pending",
                effective_env_keys=["A", "B"], effective_secret_keys=["S"],
            )
        )
    async with engine.connect() as conn:
        env = (await conn.execute(
            select(WorkflowRunModel.__table__.c.effective_env_keys).where(
                WorkflowRunModel.__table__.c.id == "r1"
            )
        )).scalar_one()
    await engine.dispose()
    assert env == ["A", "B"]  # round-trips as list, not a JSON string


@pytest.mark.asyncio
async def test_idempotency_key_conflict_target_works_on_sqlite(tmp_path):
    from sqlalchemy import func, select
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy import text as sa_text
    from sagewai.db.models import WorkflowRunModel
    t = WorkflowRunModel.__table__
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for i in range(2):
            stmt = sqlite_insert(t).values(
                id=f"r{i}", workflow_name="wf", run_id=f"run{i}",
                status="pending", idempotency_key="IDEMP-1",
            ).on_conflict_do_nothing(
                index_elements=["idempotency_key"],
                index_where=sa_text("idempotency_key IS NOT NULL"),
            )
            await conn.execute(stmt)
        count = (await conn.execute(select(func.count()).select_from(t))).scalar_one()
    await engine.dispose()
    assert count == 1  # second insert with same idempotency_key was a no-op (conflict target recognized)
