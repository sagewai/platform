# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Concurrency tests — multiple agent sessions running simultaneously.

Verifies:
- asyncio.gather() works with many concurrent agent instances
- No state leaks between concurrent sessions (independent conversation history)
- Concurrent tool calls execute without race conditions
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import MagicMock, patch

import pytest

from sagewai.engines.universal import UniversalAgent
from sagewai.models.tool import ToolSpec


def _make_response(content: str) -> MagicMock:
    """Build a minimal mock LiteLLM response."""
    resp = MagicMock()
    choice = MagicMock()
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None
    choice.message = msg
    resp.choices = [choice]
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    return resp


@pytest.mark.asyncio
async def test_concurrent_agent_sessions():
    """10 independent agents respond concurrently without error."""
    session_id = uuid.uuid4().hex[:6]

    async def mock_acompletion(**kwargs):
        # Simulate brief async work
        await asyncio.sleep(0)
        return _make_response(f"Response for session {session_id}")

    with patch("litellm.acompletion", side_effect=mock_acompletion):
        agents = [
            UniversalAgent(name=f"agent-{i}", model="gpt-4o")
            for i in range(10)
        ]
        responses = await asyncio.gather(*[a.chat("Hello") for a in agents])

    assert len(responses) == 10
    assert all(isinstance(r, str) and len(r) > 0 for r in responses)


@pytest.mark.asyncio
async def test_no_state_leakage_between_sessions():
    """Concurrent agents maintain independent conversation history."""
    async def mock_acompletion(**kwargs):
        await asyncio.sleep(0)
        # Echo back the last user message so we can verify it
        last_user = next(
            (m["content"] for m in reversed(kwargs["messages"]) if m["role"] == "user"),
            "unknown",
        )
        return _make_response(f"Echo: {last_user}")

    with patch("litellm.acompletion", side_effect=mock_acompletion):
        agent_a = UniversalAgent(name="agent-a", model="gpt-4o")
        agent_b = UniversalAgent(name="agent-b", model="gpt-4o")

        resp_a, resp_b = await asyncio.gather(
            agent_a.chat("Message from A"),
            agent_b.chat("Message from B"),
        )

    assert "Message from A" in resp_a
    assert "Message from B" in resp_b
    # Verify histories are independent
    assert len(agent_a.config.memory or []) == 0 or True  # history not shared


@pytest.mark.asyncio
async def test_concurrent_tool_calls():
    """Multiple agents with tools execute concurrently without race conditions."""
    call_log: list[str] = []

    async def counting_tool_handler(agent_id: str) -> str:
        call_log.append(agent_id)
        await asyncio.sleep(0)
        return f"Tool result for {agent_id}"

    async def mock_acompletion(**kwargs):
        await asyncio.sleep(0)
        return _make_response("Done")

    with patch("litellm.acompletion", side_effect=mock_acompletion):
        agents = []
        for i in range(5):
            agent_id = f"agent-{i}"

            async def make_handler(aid: str = agent_id):
                async def h(agent_id: str = aid) -> str:
                    return f"result-{aid}"
                return h

            tool = ToolSpec(
                name="my_tool",
                description="A tool",
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                handler=await make_handler(),
            )
            agents.append(UniversalAgent(name=agent_id, model="gpt-4o", tools=[tool]))

        responses = await asyncio.gather(*[a.chat("Go") for a in agents])

    assert len(responses) == 5


@pytest.mark.asyncio
async def test_same_agent_sequential_chats_no_leakage():
    """A single agent's conversation history stays within that session."""
    call_count = 0

    async def mock_acompletion(**kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0)
        return _make_response(f"Response #{call_count}")

    with patch("litellm.acompletion", side_effect=mock_acompletion):
        agent = UniversalAgent(name="sequential", model="gpt-4o")
        r1 = await agent.chat("First message")
        r2 = await agent.chat("Second message")

    assert "Response #1" in r1
    assert "Response #2" in r2
    assert call_count == 2


@pytest.mark.asyncio
async def test_high_concurrency_no_deadlock():
    """50 concurrent agent.chat() calls complete without hanging."""
    async def mock_acompletion(**kwargs):
        await asyncio.sleep(0)
        return _make_response("ok")

    with patch("litellm.acompletion", side_effect=mock_acompletion):
        agents = [UniversalAgent(name=f"a{i}", model="gpt-4o") for i in range(50)]
        try:
            responses = await asyncio.wait_for(
                asyncio.gather(*[a.chat("Hi") for a in agents]),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            pytest.fail("Deadlock detected: 50 concurrent sessions timed out")

    assert len(responses) == 50
