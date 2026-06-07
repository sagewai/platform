# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for PromptStore — runs against both SQLite and Postgres."""

import pytest
from sqlalchemy import insert

from sagewai.db.models import AgentRun
from sagewai.observability.prompt_store import PromptStore


async def _insert_agent_run(engine, run_id: str, project_id: str = "p1") -> None:
    """Insert a minimal agent_run row to satisfy the FK constraint on prompt_logs.run_id."""
    stmt = insert(AgentRun.__table__).values(
        run_id=run_id,
        project_id=project_id,
        agent_name="stub",
    )
    async with engine.begin() as conn:
        await conn.execute(stmt)


@pytest.mark.asyncio
async def test_save_and_get(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    log_id = await store.save_prompt_log(
        agent_name="scout",
        step_index=1,
        model="gpt-4o",
        prompt_messages=[{"role": "user", "content": "Find AI news"}],
        response_message={"role": "assistant", "content": "Here are the latest..."},
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.00075,
        duration_ms=230,
        strategy="react",
        metadata={"template_version": "v1"},
        project_id="p1",
    )
    assert isinstance(log_id, str) and len(log_id) == 12

    record = await store.get_prompt_log(log_id)
    assert record is not None
    assert record.log_id == log_id
    assert record.agent_name == "scout"
    assert record.step_index == 1
    assert record.model == "gpt-4o"
    # JSON columns round-trip as Python objects — not strings
    assert record.prompt_messages == [{"role": "user", "content": "Find AI news"}]
    assert record.response_message == {"role": "assistant", "content": "Here are the latest..."}
    assert record.input_tokens == 100
    assert record.output_tokens == 50
    assert record.cost_usd == pytest.approx(0.00075)
    assert record.duration_ms == 230
    assert record.strategy == "react"
    # metadata column (ORM attr = metadata_) round-trips as dict
    assert record.metadata == {"template_version": "v1"}
    assert record.created_at > 0


@pytest.mark.asyncio
async def test_get_nonexistent(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    assert await store.get_prompt_log("doesnotexist") is None


@pytest.mark.asyncio
async def test_list_by_run_id(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    # Must satisfy FK constraint: prompt_logs.run_id → agent_runs.run_id
    await _insert_agent_run(dialect_engine, "run-abc", project_id="p1")
    await _insert_agent_run(dialect_engine, "run-xyz", project_id="p1")
    await store.save_prompt_log(agent_name="scout", run_id="run-abc", project_id="p1")
    await store.save_prompt_log(agent_name="scout", run_id="run-xyz", project_id="p1")
    await store.save_prompt_log(agent_name="writer", run_id="run-abc", project_id="p1")

    hits = await store.list_prompt_logs(run_id="run-abc", project_id="p1")
    assert len(hits) == 2
    assert all(r.run_id == "run-abc" for r in hits)


@pytest.mark.asyncio
async def test_list_by_agent_name(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    await store.save_prompt_log(agent_name="scout", project_id="p2")
    await store.save_prompt_log(agent_name="writer", project_id="p2")
    await store.save_prompt_log(agent_name="scout", project_id="p2")

    hits = await store.list_prompt_logs(agent_name="scout", project_id="p2")
    assert len(hits) == 2
    assert all(r.agent_name == "scout" for r in hits)


@pytest.mark.asyncio
async def test_list_by_model(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    await store.save_prompt_log(agent_name="a", model="gpt-4o", project_id="p3")
    await store.save_prompt_log(agent_name="a", model="claude-3-5-sonnet", project_id="p3")
    await store.save_prompt_log(agent_name="a", model="gpt-4o", project_id="p3")

    hits = await store.list_prompt_logs(model="gpt-4o", project_id="p3")
    assert len(hits) == 2
    assert all(r.model == "gpt-4o" for r in hits)


@pytest.mark.asyncio
async def test_list_project_isolation(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    await store.save_prompt_log(agent_name="a", project_id="proj-A")
    await store.save_prompt_log(agent_name="b", project_id="proj-B")

    hits_a = await store.list_prompt_logs(project_id="proj-A")
    hits_b = await store.list_prompt_logs(project_id="proj-B")
    assert len(hits_a) == 1 and hits_a[0].agent_name == "a"
    assert len(hits_b) == 1 and hits_b[0].agent_name == "b"


@pytest.mark.asyncio
async def test_list_limit_offset(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    for i in range(5):
        await store.save_prompt_log(agent_name="a", step_index=i, project_id="pager")

    page1 = await store.list_prompt_logs(project_id="pager", limit=2, offset=0)
    page2 = await store.list_prompt_logs(project_id="pager", limit=2, offset=2)
    page3 = await store.list_prompt_logs(project_id="pager", limit=2, offset=4)
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1


@pytest.mark.asyncio
async def test_list_ordered_desc(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    for i in range(3):
        await store.save_prompt_log(agent_name="a", step_index=i, project_id="ord")

    logs = await store.list_prompt_logs(project_id="ord")
    assert len(logs) == 3
    assert logs[0].created_at >= logs[1].created_at >= logs[2].created_at


@pytest.mark.asyncio
async def test_update_tags_and_is_example(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    log_id = await store.save_prompt_log(agent_name="scout", project_id="upd")

    updated = await store.update_prompt_log(log_id, tags=["gold"], is_example=True)
    assert updated is not None
    assert updated.tags == ["gold"]
    assert updated.is_example is True

    # Re-fetch confirms persistence
    fetched = await store.get_prompt_log(log_id)
    assert fetched is not None
    assert fetched.tags == ["gold"]
    assert fetched.is_example is True


@pytest.mark.asyncio
async def test_update_noop_returns_record(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    log_id = await store.save_prompt_log(agent_name="scout", project_id="noop")
    rec = await store.update_prompt_log(log_id)
    assert rec is not None
    assert rec.log_id == log_id


@pytest.mark.asyncio
async def test_delete(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    log_id = await store.save_prompt_log(agent_name="scout", project_id="del")
    assert await store.delete_prompt_log(log_id) is True
    assert await store.get_prompt_log(log_id) is None


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_false(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    assert await store.delete_prompt_log("nope-never-existed") is False


@pytest.mark.asyncio
async def test_list_examples(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    await store.save_prompt_log(agent_name="scout", is_example=True, project_id="ex")
    await store.save_prompt_log(agent_name="scout", is_example=False, project_id="ex")
    await store.save_prompt_log(agent_name="writer", is_example=True, project_id="ex")

    examples = await store.list_examples(agent_name="scout", project_id="ex")
    assert len(examples) == 1
    assert examples[0].is_example is True


@pytest.mark.asyncio
async def test_json_roundtrip_complex(dialect_engine):
    """Nested JSON objects must survive a save/get cycle as Python objects."""
    store = PromptStore(engine=dialect_engine)
    msgs = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "Hello", "extra": {"nested": True}},
    ]
    resp = {"role": "assistant", "content": "Hi", "tool_calls": [{"id": "tc1"}]}
    meta = {"key": "value", "nested": {"count": 42}, "arr": [1, 2, 3]}
    tags_in = ["gold", "reviewed"]

    log_id = await store.save_prompt_log(
        agent_name="complex",
        prompt_messages=msgs,
        response_message=resp,
        metadata=meta,
        tags=tags_in,
        project_id="json",
    )
    rec = await store.get_prompt_log(log_id)
    assert rec is not None
    assert rec.prompt_messages == msgs
    assert rec.response_message == resp
    assert rec.metadata == meta
    assert rec.tags == tags_in


@pytest.mark.asyncio
async def test_is_connected_true_after_construct(dialect_engine):
    store = PromptStore(engine=dialect_engine)
    assert store.is_connected is True


@pytest.mark.asyncio
async def test_export_jsonl(dialect_engine):
    import json

    store = PromptStore(engine=dialect_engine)
    await store.save_prompt_log(agent_name="a", step_index=0, project_id="jl")
    await store.save_prompt_log(agent_name="a", step_index=1, project_id="jl")
    logs = await store.list_prompt_logs(project_id="jl")
    jsonl = store.export_jsonl(logs)
    lines = jsonl.strip().split("\n")
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["agent_name"] == "a"
