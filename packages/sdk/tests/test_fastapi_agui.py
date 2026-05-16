# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for AG-UI FastAPI integration router."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.core.base import BaseAgent
from sagewai.integrations.fastapi import AGUIRouter
from sagewai.models.message import ChatMessage, ToolCall
from sagewai.models.tool import ToolSpec


class MockAgent(BaseAgent):
    """Agent that returns predetermined responses."""

    def __init__(self, responses: list[ChatMessage], **kwargs: Any):
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


def _create_app(agent: BaseAgent) -> FastAPI:
    """Helper to create a FastAPI app with AG-UI router."""
    app = FastAPI()
    agui = AGUIRouter(prefix="/agui")
    agui.register_agent(agent)
    app.include_router(agui.router)
    return app


def _parse_sse_events(response_text: str) -> list[dict]:
    """Parse SSE response text into list of event dicts."""
    events = []
    current_type = None
    for line in response_text.strip().split("\n"):
        if line.startswith("event: "):
            current_type = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            data["_sse_event"] = current_type
            events.append(data)
            current_type = None
    return events


# ------------------------------------------------------------------
# SSE endpoint tests
# ------------------------------------------------------------------


def test_sse_run_lifecycle():
    """POST /agui/runs returns SSE stream with RUN_STARTED and RUN_FINISHED."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("Hello!")],
        name="test-agent",
        model="mock",
    )
    app = _create_app(agent)

    with TestClient(app) as client:
        response = client.post(
            "/agui/runs",
            json={"message": "Hi", "threadId": "t1"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse_events(response.text)
        event_types = [e["_sse_event"] for e in events]

        assert event_types[0] == "RUN_STARTED"
        assert event_types[-1] == "RUN_FINISHED"

        # Check RUN_STARTED payload
        start = events[0]
        assert start["threadId"] == "t1"
        assert "runId" in start
        assert start["input"] == {"message": "Hi"}

        # Check RUN_FINISHED payload
        finish = events[-1]
        assert finish["result"] == "Hello!"


def test_sse_includes_step_events():
    """SSE stream includes STEP_STARTED and STEP_FINISHED from strategy."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("Done")],
        name="step-agent",
        model="mock",
    )
    app = _create_app(agent)

    with TestClient(app) as client:
        response = client.post("/agui/runs", json={"message": "Go"})
        events = _parse_sse_events(response.text)
        event_types = [e["_sse_event"] for e in events]

        assert "STEP_STARTED" in event_types
        assert "STEP_FINISHED" in event_types


def test_sse_includes_tool_events():
    """SSE stream includes tool call events when agent uses tools."""

    async def mock_search(query: str) -> str:
        return "found"

    tool_spec = ToolSpec(
        name="search",
        description="Search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=mock_search,
    )

    agent = MockAgent(
        responses=[
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="tc1", name="search", arguments={"query": "test"})]
            ),
            ChatMessage.assistant("Found it"),
        ],
        name="tool-agent",
        model="mock",
        tools=[tool_spec],
    )
    app = _create_app(agent)

    with TestClient(app) as client:
        response = client.post("/agui/runs", json={"message": "Search"})
        events = _parse_sse_events(response.text)
        event_types = [e["_sse_event"] for e in events]

        assert "TOOL_CALL_START" in event_types
        assert "TOOL_CALL_END" in event_types
        assert "TOOL_CALL_RESULT" in event_types

        # Check tool call payload
        tc_start = next(e for e in events if e["_sse_event"] == "TOOL_CALL_START")
        assert tc_start["toolCallId"] == "tc1"
        assert tc_start["toolCallName"] == "search"


def test_sse_auto_generates_thread_id():
    """Thread ID is auto-generated when not provided."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("OK")],
        name="auto-id",
        model="mock",
    )
    app = _create_app(agent)

    with TestClient(app) as client:
        response = client.post("/agui/runs", json={"message": "Hi"})
        events = _parse_sse_events(response.text)
        start = events[0]
        assert len(start["threadId"]) > 0


def test_sse_text_content_events():
    """SSE stream includes TEXT_MESSAGE_CONTENT from strategy."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("Hello world")],
        name="text-agent",
        model="mock",
    )
    app = _create_app(agent)

    with TestClient(app) as client:
        response = client.post("/agui/runs", json={"message": "Hi"})
        events = _parse_sse_events(response.text)
        event_types = [e["_sse_event"] for e in events]

        assert "TEXT_MESSAGE_CONTENT" in event_types
        text_event = next(e for e in events if e["_sse_event"] == "TEXT_MESSAGE_CONTENT")
        assert text_event["delta"] == "Hello world"


# ------------------------------------------------------------------
# WebSocket endpoint tests
# ------------------------------------------------------------------


def test_websocket_run_lifecycle():
    """WebSocket /agui/ws streams AG-UI events for a run."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("Hello!")],
        name="ws-agent",
        model="mock",
    )
    app = _create_app(agent)

    with TestClient(app) as client:
        with client.websocket_connect("/agui/ws") as ws:
            ws.send_json({"message": "Hi", "threadId": "t1"})

            events = []
            # Collect all events until RUN_FINISHED
            while True:
                data = json.loads(ws.receive_text())
                events.append(data)
                if data.get("type") == "RUN_FINISHED":
                    break

            event_types = [e["type"] for e in events]
            assert event_types[0] == "RUN_STARTED"
            assert event_types[-1] == "RUN_FINISHED"

            start = events[0]
            assert start["threadId"] == "t1"
            assert start["input"] == {"message": "Hi"}


# ------------------------------------------------------------------
# Agent resolution
# ------------------------------------------------------------------


def test_multiple_agents():
    """Router resolves agents by name."""
    agent1 = MockAgent(
        responses=[ChatMessage.assistant("From agent 1")],
        name="agent-1",
        model="mock",
    )
    agent2 = MockAgent(
        responses=[ChatMessage.assistant("From agent 2")],
        name="agent-2",
        model="mock",
    )

    app = FastAPI()
    agui = AGUIRouter(prefix="/agui")
    agui.register_agent(agent1)
    agui.register_agent(agent2)
    app.include_router(agui.router)

    with TestClient(app) as client:
        response = client.post(
            "/agui/runs",
            json={"message": "Hi", "agentName": "agent-2"},
        )
        events = _parse_sse_events(response.text)
        finish = next(e for e in events if e["_sse_event"] == "RUN_FINISHED")
        assert finish["result"] == "From agent 2"


def test_default_agent_when_no_name():
    """When no agent name specified, uses the first registered agent."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("Default")],
        name="default",
        model="mock",
    )
    app = _create_app(agent)

    with TestClient(app) as client:
        response = client.post("/agui/runs", json={"message": "Hi"})
        events = _parse_sse_events(response.text)
        finish = next(e for e in events if e["_sse_event"] == "RUN_FINISHED")
        assert finish["result"] == "Default"


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


def test_no_agents_registered():
    """Error when no agents registered."""
    app = FastAPI()
    agui = AGUIRouter()
    app.include_router(agui.router)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/agui/runs", json={"message": "Hi"})
        assert response.status_code == 500


# ------------------------------------------------------------------
# Listener cleanup
# ------------------------------------------------------------------


def test_listener_cleanup_after_sse():
    """Event listeners are removed after SSE stream completes."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("OK")] * 3,
        name="cleanup",
        model="mock",
    )
    app = _create_app(agent)
    initial_count = len(agent._event_listeners)

    with TestClient(app) as client:
        client.post("/agui/runs", json={"message": "1"})
        client.post("/agui/runs", json={"message": "2"})

    # Listeners should be cleaned up
    assert len(agent._event_listeners) == initial_count
