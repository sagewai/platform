# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for RunStore — PostgreSQL-backed agent run persistence."""

from __future__ import annotations

import os

import pytest

from sagewai.admin.store import RunRecord, RunStore

# ---------------------------------------------------------------------------
# RunRecord
# ---------------------------------------------------------------------------


class TestRunRecord:
    def test_defaults(self):
        rec = RunRecord(run_id="abc", agent_name="agent")
        assert rec.status == "completed"
        assert rec.total_tokens == 0
        assert rec.tool_calls == []

    def test_to_dict(self):
        rec = RunRecord(
            run_id="abc",
            agent_name="agent",
            total_tokens=100,
            tool_calls=[{"tool_name": "search"}],
        )
        d = rec.to_dict()
        assert d["run_id"] == "abc"
        assert d["total_tokens"] == 100
        assert len(d["tool_calls"]) == 1


# ---------------------------------------------------------------------------
# RunStore — init and teardown
# ---------------------------------------------------------------------------


@pytest.fixture
async def store():
    """RunStore connected to the local dev PostgreSQL.

    Requires: make dev-up (or a running PostgreSQL on localhost:5432).
    Cleans up after each test.
    """
    url = os.environ.get(
        "SAGEWAI_DATABASE_URL",
        "postgresql://sagecurator:sagecurator_password@localhost:5432/sagecurator",
    )
    s = RunStore(url)
    await s.init()
    await s.clear()  # Start clean
    yield s
    await s.clear()  # Clean up
    await s.close()


@pytest.mark.integration
class TestInit:
    @pytest.mark.asyncio
    async def test_init_postgres(self):
        url = os.environ.get(
            "SAGEWAI_DATABASE_URL",
            "postgresql://sagecurator:sagecurator_password@localhost:5432/sagecurator",
        )
        store = RunStore(url)
        await store.init()
        assert store.is_connected
        await store.close()
        assert not store.is_connected

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        store = RunStore("postgresql://localhost/test")
        with pytest.raises(RuntimeError, match="not initialized"):
            await store.save_run(agent_name="test")


# ---------------------------------------------------------------------------
# Save and retrieve
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSaveAndRetrieve:
    @pytest.mark.asyncio
    async def test_save_run(self, store):
        run_id = await store.save_run(
            agent_name="scout",
            input_text="find AI trends",
            output_text="here are the trends",
            total_tokens=500,
            model="gpt-4o",
        )
        assert len(run_id) == 12

    @pytest.mark.asyncio
    async def test_get_run(self, store):
        run_id = await store.save_run(
            agent_name="scout",
            input_text="hello",
            output_text="world",
            total_tokens=100,
            input_tokens=30,
            output_tokens=70,
            cost_usd=0.005,
            model="gpt-4o",
            duration_ms=1500,
        )
        run = await store.get_run(run_id)
        assert run is not None
        assert run.agent_name == "scout"
        assert run.input_text == "hello"
        assert run.output_text == "world"
        assert run.total_tokens == 100
        assert run.input_tokens == 30
        assert run.output_tokens == 70
        assert run.cost_usd == 0.005
        assert run.model == "gpt-4o"
        assert run.duration_ms == 1500

    @pytest.mark.asyncio
    async def test_get_run_not_found(self, store):
        assert await store.get_run("nonexistent") is None

    @pytest.mark.asyncio
    async def test_save_with_tool_calls(self, store):
        run_id = await store.save_run(
            agent_name="agent",
            tool_calls=[
                {"tool_name": "search", "args": '{"q": "test"}'},
                {"tool_name": "calc", "args": '{"expr": "1+1"}'},
            ],
        )
        run = await store.get_run(run_id)
        assert len(run.tool_calls) == 2
        assert run.tool_calls[0]["tool_name"] == "search"

    @pytest.mark.asyncio
    async def test_save_with_metadata(self, store):
        run_id = await store.save_run(
            agent_name="agent",
            metadata={"session_id": "s123", "user": "test"},
        )
        run = await store.get_run(run_id)
        assert run.metadata["session_id"] == "s123"

    @pytest.mark.asyncio
    async def test_save_with_error(self, store):
        run_id = await store.save_run(
            agent_name="agent",
            status="failed",
            error="Something went wrong",
        )
        run = await store.get_run(run_id)
        assert run.status == "failed"
        assert run.error == "Something went wrong"


# ---------------------------------------------------------------------------
# Listing and filtering
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListAndFilter:
    @pytest.mark.asyncio
    async def test_list_all(self, store):
        await store.save_run(agent_name="a1")
        await store.save_run(agent_name="a2")
        await store.save_run(agent_name="a3")
        runs = await store.list_runs()
        assert len(runs) == 3

    @pytest.mark.asyncio
    async def test_filter_by_agent(self, store):
        await store.save_run(agent_name="scout")
        await store.save_run(agent_name="writer")
        await store.save_run(agent_name="scout")
        runs = await store.list_runs(agent_name="scout")
        assert len(runs) == 2

    @pytest.mark.asyncio
    async def test_filter_by_status(self, store):
        await store.save_run(agent_name="a", status="completed")
        await store.save_run(agent_name="a", status="failed")
        runs = await store.list_runs(status="failed")
        assert len(runs) == 1

    @pytest.mark.asyncio
    async def test_filter_by_model(self, store):
        await store.save_run(agent_name="a", model="gpt-4o")
        await store.save_run(agent_name="a", model="claude-3.5-sonnet")
        runs = await store.list_runs(model="gpt-4o")
        assert len(runs) == 1

    @pytest.mark.asyncio
    async def test_filter_by_time_range(self, store):
        await store.save_run(agent_name="a", started_at=1000.0)
        await store.save_run(agent_name="a", started_at=2000.0)
        await store.save_run(agent_name="a", started_at=3000.0)
        runs = await store.list_runs(since=1500.0, until=2500.0)
        assert len(runs) == 1

    @pytest.mark.asyncio
    async def test_pagination(self, store):
        for i in range(10):
            await store.save_run(agent_name="a", started_at=float(i))
        page1 = await store.list_runs(limit=3, offset=0)
        page2 = await store.list_runs(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        # No overlap
        ids1 = {r.run_id for r in page1}
        ids2 = {r.run_id for r in page2}
        assert ids1.isdisjoint(ids2)

    @pytest.mark.asyncio
    async def test_ordered_by_most_recent(self, store):
        await store.save_run(agent_name="a", started_at=1.0)
        await store.save_run(agent_name="a", started_at=3.0)
        await store.save_run(agent_name="a", started_at=2.0)
        runs = await store.list_runs()
        assert runs[0].started_at == 3.0
        assert runs[1].started_at == 2.0
        assert runs[2].started_at == 1.0


# ---------------------------------------------------------------------------
# Delete and count
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeleteAndCount:
    @pytest.mark.asyncio
    async def test_count(self, store):
        await store.save_run(agent_name="a")
        await store.save_run(agent_name="a")
        await store.save_run(agent_name="b")
        assert await store.count() == 3
        assert await store.count(agent_name="a") == 2
        assert await store.count(status="completed") == 3

    @pytest.mark.asyncio
    async def test_delete_run(self, store):
        run_id = await store.save_run(agent_name="a")
        assert await store.count() == 1
        await store.delete_run(run_id)
        assert await store.get_run(run_id) is None

    @pytest.mark.asyncio
    async def test_clear(self, store):
        await store.save_run(agent_name="a")
        await store.save_run(agent_name="b")
        await store.clear()
        assert await store.count() == 0


# ---------------------------------------------------------------------------
# Event hook
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEventHook:
    @pytest.mark.asyncio
    async def test_create_event_hook(self, store):
        hook = store.create_event_hook()
        assert callable(hook)

    @pytest.mark.asyncio
    async def test_event_hook_records_run(self, store):
        from sagewai.core.events import AgentEvent

        hook = store.create_event_hook()
        await hook(AgentEvent.RUN_STARTED, {"agent": "scout"})
        await hook(
            AgentEvent.LLM_CALL_FINISHED,
            {
                "agent": "scout",
                "model": "gpt-4o",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.00075,
                "duration_ms": 200.0,
            },
        )
        await hook(
            AgentEvent.LLM_CALL_FINISHED,
            {
                "agent": "scout",
                "model": "gpt-4o",
                "input_tokens": 80,
                "output_tokens": 40,
                "cost_usd": 0.0006,
                "duration_ms": 150.0,
            },
        )
        await hook(AgentEvent.RUN_FINISHED, {"agent": "scout", "input": "hi", "output": "bye"})

        runs = await store.list_runs()
        assert len(runs) == 1
        run = runs[0]
        assert run.agent_name == "scout"
        assert run.input_tokens == 180
        assert run.output_tokens == 90
        assert run.total_tokens == 270
        assert run.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_event_hook_accumulates_tool_calls(self, store):
        from sagewai.core.events import AgentEvent

        hook = store.create_event_hook()
        await hook(AgentEvent.RUN_STARTED, {"agent": "agent"})
        await hook(
            AgentEvent.TOOL_CALL_RESULT,
            {"agent": "agent", "tool_name": "search", "tool_call_id": "tc1"},
        )
        await hook(AgentEvent.RUN_FINISHED, {"agent": "agent"})

        runs = await store.list_runs()
        assert len(runs[0].tool_calls) == 1
        assert runs[0].tool_calls[0]["tool_name"] == "search"
