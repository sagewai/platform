# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for per-step prompt logging (PromptStore + PromptLogRecord)."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage, UsageInfo
from sagewai.models.tool import ToolSpec
from sagewai.observability.prompt_store import PromptLogRecord, PromptStore

PG_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    "postgresql://sagecurator:sagecurator_password@localhost:5432/sagecurator",
)

# ------------------------------------------------------------------
# PromptLogRecord dataclass
# ------------------------------------------------------------------


def test_prompt_log_record_defaults():
    record = PromptLogRecord(log_id="abc", run_id="run1", agent_name="scout")
    assert record.log_id == "abc"
    assert record.run_id == "run1"
    assert record.agent_name == "scout"
    assert record.step_index == 0
    assert record.model == ""
    assert record.prompt_messages == []
    assert record.response_message == {}
    assert record.input_tokens == 0
    assert record.output_tokens == 0
    assert record.cost_usd == 0.0
    assert record.duration_ms == 0
    assert record.strategy == "react"
    assert record.metadata == {}


def test_prompt_log_record_to_dict():
    record = PromptLogRecord(
        log_id="abc",
        run_id="run1",
        agent_name="scout",
        step_index=2,
        model="gpt-4o",
        prompt_messages=[{"role": "user", "content": "Hello"}],
        response_message={"role": "assistant", "content": "Hi"},
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.001,
        duration_ms=150,
        strategy="react",
        metadata={"key": "value"},
        created_at=1000.0,
    )
    d = record.to_dict()
    assert d["log_id"] == "abc"
    assert d["run_id"] == "run1"
    assert d["agent_name"] == "scout"
    assert d["step_index"] == 2
    assert d["model"] == "gpt-4o"
    assert d["prompt_messages"] == [{"role": "user", "content": "Hello"}]
    assert d["response_message"] == {"role": "assistant", "content": "Hi"}
    assert d["input_tokens"] == 10
    assert d["output_tokens"] == 5
    assert d["cost_usd"] == 0.001
    assert d["duration_ms"] == 150
    assert d["strategy"] == "react"
    assert d["metadata"] == {"key": "value"}
    assert d["created_at"] == 1000.0


# ------------------------------------------------------------------
# PromptStore lifecycle
# ------------------------------------------------------------------


@pytest.fixture
async def store():
    """PromptStore connected to the local dev PostgreSQL."""
    s = PromptStore(PG_URL)
    await s.init()
    # Clean prompt_logs before each test
    await s._pool.execute("DELETE FROM prompt_logs")
    yield s
    await s._pool.execute("DELETE FROM prompt_logs")
    await s.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_prompt_store_init_close():
    s = PromptStore(PG_URL)
    assert not s.is_connected
    await s.init()
    assert s.is_connected
    await s.close()
    assert not s.is_connected


@pytest.mark.integration
@pytest.mark.asyncio
async def test_prompt_store_not_initialized_raises():
    s = PromptStore(PG_URL)
    with pytest.raises(RuntimeError, match="not initialized"):
        await s.save_prompt_log(agent_name="test")


# ------------------------------------------------------------------
# save / get round-trip
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_save_and_get_prompt_log(store):
    log_id = await store.save_prompt_log(
        run_id="",
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
    )

    assert isinstance(log_id, str)
    assert len(log_id) == 12

    record = await store.get_prompt_log(log_id)
    assert record is not None
    assert record.log_id == log_id
    assert record.agent_name == "scout"
    assert record.step_index == 1
    assert record.model == "gpt-4o"
    assert record.prompt_messages == [{"role": "user", "content": "Find AI news"}]
    assert record.response_message == {"role": "assistant", "content": "Here are the latest..."}
    assert record.input_tokens == 100
    assert record.output_tokens == 50
    assert record.cost_usd == 0.00075
    assert record.duration_ms == 230
    assert record.strategy == "react"
    assert record.metadata == {"template_version": "v1"}
    assert record.created_at > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_nonexistent_prompt_log(store):
    record = await store.get_prompt_log("nonexistent")
    assert record is None


# ------------------------------------------------------------------
# list_prompt_logs filtering
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_prompt_logs_all(store):
    await store.save_prompt_log(run_id="", agent_name="scout", step_index=0)
    await store.save_prompt_log(run_id="", agent_name="scout", step_index=1)
    await store.save_prompt_log(run_id="", agent_name="writer", step_index=0)

    logs = await store.list_prompt_logs()
    assert len(logs) == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_prompt_logs_by_agent_name(store):
    await store.save_prompt_log(run_id="", agent_name="scout")
    await store.save_prompt_log(run_id="", agent_name="creator")

    logs = await store.list_prompt_logs(agent_name="scout")
    assert len(logs) == 1
    assert logs[0].agent_name == "scout"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_prompt_logs_by_model(store):
    await store.save_prompt_log(run_id="", agent_name="a", model="gpt-4o")
    await store.save_prompt_log(run_id="", agent_name="a", model="claude-3-5-sonnet-20241022")
    await store.save_prompt_log(run_id="", agent_name="a", model="gpt-4o")

    logs = await store.list_prompt_logs(model="gpt-4o")
    assert len(logs) == 2
    assert all(r.model == "gpt-4o" for r in logs)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_prompt_logs_ordered_by_created_at(store):
    await store.save_prompt_log(run_id="", agent_name="a", step_index=0)
    await store.save_prompt_log(run_id="", agent_name="a", step_index=1)
    await store.save_prompt_log(run_id="", agent_name="a", step_index=2)

    logs = await store.list_prompt_logs()
    assert len(logs) == 3
    # created_at should be non-increasing (DESC order — newest first)
    assert logs[0].created_at >= logs[1].created_at >= logs[2].created_at


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_prompt_logs_limit_offset(store):
    for i in range(5):
        await store.save_prompt_log(run_id="", agent_name="a", step_index=i)

    logs = await store.list_prompt_logs(limit=2, offset=0)
    assert len(logs) == 2

    logs_page2 = await store.list_prompt_logs(limit=2, offset=2)
    assert len(logs_page2) == 2

    logs_page3 = await store.list_prompt_logs(limit=2, offset=4)
    assert len(logs_page3) == 1


# ------------------------------------------------------------------
# export_jsonl
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_export_jsonl(store):
    await store.save_prompt_log(
        run_id="",
        agent_name="scout",
        step_index=0,
        model="gpt-4o",
        prompt_messages=[{"role": "user", "content": "Hello"}],
        response_message={"role": "assistant", "content": "Hi"},
    )
    await store.save_prompt_log(
        run_id="",
        agent_name="scout",
        step_index=1,
        model="gpt-4o",
        prompt_messages=[{"role": "user", "content": "Bye"}],
        response_message={"role": "assistant", "content": "Goodbye"},
    )

    logs = await store.list_prompt_logs()
    jsonl = store.export_jsonl(logs)

    lines = jsonl.strip().split("\n")
    assert len(lines) == 2

    # list_prompt_logs returns DESC order (newest first)
    first = json.loads(lines[0])
    assert first["agent_name"] == "scout"
    assert first["step_index"] == 1
    assert first["prompt_messages"] == [{"role": "user", "content": "Bye"}]

    second = json.loads(lines[1])
    assert second["step_index"] == 0


def test_export_jsonl_empty():
    store = PromptStore(PG_URL)
    assert store.export_jsonl([]) == ""


# ------------------------------------------------------------------
# Event hook integration
# ------------------------------------------------------------------


class MockAgent(BaseAgent):
    """Agent that returns predetermined responses."""

    def __init__(self, responses: list[ChatMessage], **kwargs: Any):
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0

    async def _invoke_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        model_override: str | None = None,
    ) -> ChatMessage:
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


@pytest.mark.integration
@pytest.mark.asyncio
async def test_event_hook_records_prompt_logged(store):
    """Event hook auto-records from PROMPT_LOGGED events."""
    agent = MockAgent(
        responses=[
            ChatMessage.assistant(
                "Hello!",
                usage=UsageInfo(input_tokens=10, output_tokens=5),
            )
        ],
        name="hook-test",
        model="gpt-4o",
    )
    agent.on_event(store.create_event_hook())

    await agent.chat("Hi")

    logs = await store.list_prompt_logs(agent_name="hook-test")
    assert len(logs) == 1
    assert logs[0].agent_name == "hook-test"
    assert logs[0].model == "gpt-4o"
    assert logs[0].step_index == 0
    assert logs[0].strategy == "react"
    assert logs[0].input_tokens == 10
    assert logs[0].output_tokens == 5
    assert len(logs[0].prompt_messages) > 0
    assert logs[0].response_message.get("role") == "assistant"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_event_hook_multi_step(store):
    """Event hook records one log per iteration in a multi-step agent loop."""

    async def mock_tool(query: str) -> str:
        return "result"

    tool_spec = ToolSpec(
        name="search",
        description="Search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=mock_tool,
    )

    from sagewai.models.message import ToolCall

    agent = MockAgent(
        responses=[
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="tc1", name="search", arguments={"query": "test"})],
                usage=UsageInfo(input_tokens=20, output_tokens=10),
            ),
            ChatMessage.assistant(
                "Found it",
                usage=UsageInfo(input_tokens=30, output_tokens=15),
            ),
        ],
        name="multi-step",
        model="gpt-4o",
        tools=[tool_spec],
    )
    agent.on_event(store.create_event_hook())

    await agent.chat("Search for test")

    logs = await store.list_prompt_logs(agent_name="multi-step")
    assert len(logs) == 2
    # list_prompt_logs returns DESC order (newest first)
    assert logs[0].step_index == 1
    assert logs[1].step_index == 0


# ------------------------------------------------------------------
# PROMPT_LOGGED event in AgentEvent
# ------------------------------------------------------------------


def test_prompt_logged_event_exists():
    assert AgentEvent.PROMPT_LOGGED.value == "prompt_logged"


def test_agent_event_count():
    assert len(AgentEvent) == 40
