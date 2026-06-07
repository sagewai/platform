# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for RunStore — runs against both SQLite and Postgres."""

import pytest

from sagewai.admin.store import RunStore


@pytest.mark.asyncio
async def test_save_and_get(dialect_engine):
    store = RunStore(engine=dialect_engine)
    rid = await store.save_run(agent_name="scout", input_text="in", output_text="out",
                               total_tokens=10, model="m", project_id="p1",
                               tool_calls=[{"tool_name": "x"}], metadata={"k": "v"})
    rec = await store.get_run(rid, project_id="p1")
    assert rec is not None
    assert rec.agent_name == "scout" and rec.total_tokens == 10
    assert rec.tool_calls == [{"tool_name": "x"}] and rec.metadata == {"k": "v"}


@pytest.mark.asyncio
async def test_list_filters(dialect_engine):
    store = RunStore(engine=dialect_engine)
    await store.save_run(agent_name="scout", status="completed", model="m1", project_id="p1")
    await store.save_run(agent_name="writer", status="failed", model="m2", project_id="p1")
    await store.save_run(agent_name="scout", status="completed", model="m1", project_id="p2")
    p1 = await store.list_runs(project_id="p1")
    assert len(p1) == 2
    scouts = await store.list_runs(agent_name="scout", project_id="p1")
    assert len(scouts) == 1 and scouts[0].agent_name == "scout"
    failed = await store.list_runs(status="failed", project_id="p1")
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_list_exclude_run_types(dialect_engine):
    store = RunStore(engine=dialect_engine)
    await store.save_run(agent_name="a", project_id="p1", run_type="standalone")
    await store.save_run(agent_name="b", project_id="p1", run_type="workflow_step")
    out = await store.list_runs(project_id="p1", exclude_run_types=["workflow_step"])
    assert len(out) == 1 and out[0].run_type == "standalone"


@pytest.mark.asyncio
async def test_count_and_delete(dialect_engine):
    store = RunStore(engine=dialect_engine)
    rid = await store.save_run(agent_name="scout", project_id="p1")
    assert await store.count(project_id="p1") == 1
    assert await store.delete_run(rid, project_id="p1") is True
    assert await store.count(project_id="p1") == 0


@pytest.mark.asyncio
async def test_is_connected_true_after_construct(dialect_engine):
    store = RunStore(engine=dialect_engine)
    assert store.is_connected is True
