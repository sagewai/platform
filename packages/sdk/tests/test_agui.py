# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for AG-UI event model and emitter."""

from __future__ import annotations

import json

import pytest

from sagewai.protocols.agui import (
    AGUIEmitter,
    CustomEvent,
    EventType,
    MessagesSnapshotEvent,
    RawEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    SSETransport,
    StateDeltaEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    WebSocketTransport,
)
from sagewai.protocols.agui.emitter import CallbackTransport
from sagewai.protocols.agui.events import JSONPatchOperation

# ------------------------------------------------------------------
# Event type enum
# ------------------------------------------------------------------


def test_event_type_count():
    """All 17 event types are defined."""
    assert len(EventType) == 17


def test_event_type_values():
    """Event type values match AG-UI spec strings."""
    assert EventType.RUN_STARTED.value == "RUN_STARTED"
    assert EventType.TEXT_MESSAGE_CONTENT.value == "TEXT_MESSAGE_CONTENT"
    assert EventType.TOOL_CALL_START.value == "TOOL_CALL_START"
    assert EventType.STATE_DELTA.value == "STATE_DELTA"
    assert EventType.CUSTOM.value == "CUSTOM"


# ------------------------------------------------------------------
# Lifecycle events
# ------------------------------------------------------------------


def test_run_started_event():
    event = RunStartedEvent(thread_id="t1", run_id="r1", input={"message": "hi"})
    assert event.type == EventType.RUN_STARTED
    assert event.thread_id == "t1"
    assert event.run_id == "r1"
    assert event.input == {"message": "hi"}
    assert event.parent_run_id is None
    assert event.timestamp > 0


def test_run_started_serialization():
    """Serializes with camelCase aliases per AG-UI spec."""
    event = RunStartedEvent(thread_id="t1", run_id="r1", parent_run_id="p1")
    data = json.loads(event.model_dump_json(by_alias=True))
    assert data["type"] == "RUN_STARTED"
    assert data["threadId"] == "t1"
    assert data["runId"] == "r1"
    assert data["parentRunId"] == "p1"
    assert "thread_id" not in data


def test_run_finished_event():
    event = RunFinishedEvent(thread_id="t1", run_id="r1", result="done")
    assert event.type == EventType.RUN_FINISHED
    assert event.result == "done"


def test_run_error_event():
    event = RunErrorEvent(message="Something failed", code="TIMEOUT")
    assert event.type == EventType.RUN_ERROR
    assert event.message == "Something failed"
    assert event.code == "TIMEOUT"


def test_run_error_no_code():
    event = RunErrorEvent(message="Failed")
    assert event.code is None


def test_step_started_event():
    event = StepStartedEvent(step_name="research")
    assert event.type == EventType.STEP_STARTED
    data = json.loads(event.model_dump_json(by_alias=True))
    assert data["stepName"] == "research"


def test_step_finished_event():
    event = StepFinishedEvent(step_name="research")
    assert event.type == EventType.STEP_FINISHED


# ------------------------------------------------------------------
# Text message events
# ------------------------------------------------------------------


def test_text_message_start():
    event = TextMessageStartEvent(message_id="m1", role="assistant")
    assert event.type == EventType.TEXT_MESSAGE_START
    assert event.role == "assistant"
    data = json.loads(event.model_dump_json(by_alias=True))
    assert data["messageId"] == "m1"


def test_text_message_start_default_role():
    event = TextMessageStartEvent(message_id="m1")
    assert event.role == "assistant"


def test_text_message_content():
    event = TextMessageContentEvent(message_id="m1", delta="Hello ")
    assert event.type == EventType.TEXT_MESSAGE_CONTENT
    assert event.delta == "Hello "


def test_text_message_end():
    event = TextMessageEndEvent(message_id="m1")
    assert event.type == EventType.TEXT_MESSAGE_END


# ------------------------------------------------------------------
# Tool call events
# ------------------------------------------------------------------


def test_tool_call_start():
    event = ToolCallStartEvent(tool_call_id="tc1", tool_call_name="search", parent_message_id="m1")
    assert event.type == EventType.TOOL_CALL_START
    data = json.loads(event.model_dump_json(by_alias=True))
    assert data["toolCallId"] == "tc1"
    assert data["toolCallName"] == "search"
    assert data["parentMessageId"] == "m1"


def test_tool_call_args():
    event = ToolCallArgsEvent(tool_call_id="tc1", delta='{"query":')
    assert event.type == EventType.TOOL_CALL_ARGS
    assert event.delta == '{"query":'


def test_tool_call_end():
    event = ToolCallEndEvent(tool_call_id="tc1")
    assert event.type == EventType.TOOL_CALL_END


def test_tool_call_result():
    event = ToolCallResultEvent(message_id="m2", tool_call_id="tc1", content={"results": [1, 2, 3]})
    assert event.type == EventType.TOOL_CALL_RESULT
    assert event.role == "tool"
    assert event.content == {"results": [1, 2, 3]}


# ------------------------------------------------------------------
# State events
# ------------------------------------------------------------------


def test_state_snapshot():
    event = StateSnapshotEvent(snapshot={"count": 42, "items": ["a", "b"]})
    assert event.type == EventType.STATE_SNAPSHOT
    assert event.snapshot["count"] == 42


def test_state_delta_json_patch():
    """STATE_DELTA uses RFC 6902 JSON Patch operations."""
    ops = [
        JSONPatchOperation(op="replace", path="/count", value=43),
        JSONPatchOperation(op="add", path="/items/2", value="c"),
    ]
    event = StateDeltaEvent(delta=ops)
    assert event.type == EventType.STATE_DELTA
    assert len(event.delta) == 2

    data = json.loads(event.model_dump_json(by_alias=True))
    assert data["delta"][0]["op"] == "replace"
    assert data["delta"][0]["path"] == "/count"
    assert data["delta"][1]["op"] == "add"


def test_json_patch_operation_move():
    """JSON Patch 'move' operation uses 'from' field."""
    op = JSONPatchOperation(op="move", path="/new", from_path="/old")
    data = json.loads(op.model_dump_json(by_alias=True))
    assert data["from"] == "/old"


def test_messages_snapshot():
    msgs = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]
    event = MessagesSnapshotEvent(messages=msgs)
    assert event.type == EventType.MESSAGES_SNAPSHOT
    assert len(event.messages) == 2


# ------------------------------------------------------------------
# Special events
# ------------------------------------------------------------------


def test_raw_event():
    event = RawEvent(event={"raw": "data"}, source="external-api")
    assert event.type == EventType.RAW
    assert event.source == "external-api"


def test_custom_event():
    event = CustomEvent(name="user_feedback", value={"rating": 5})
    assert event.type == EventType.CUSTOM
    assert event.name == "user_feedback"
    assert event.value == {"rating": 5}


# ------------------------------------------------------------------
# Emitter + Transports
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_transport():
    """CallbackTransport collects events in memory."""
    transport = CallbackTransport()
    emitter = AGUIEmitter(transport=transport)

    await emitter.emit(RunStartedEvent(thread_id="t1", run_id="r1"))
    await emitter.emit(RunFinishedEvent(thread_id="t1", run_id="r1"))

    assert len(transport.events) == 2
    assert transport.events[0].type == EventType.RUN_STARTED
    assert transport.events[1].type == EventType.RUN_FINISHED


@pytest.mark.asyncio
async def test_sse_transport():
    """SSETransport formats events as SSE messages."""
    sent: list[str] = []

    async def mock_send(data: str) -> None:
        sent.append(data)

    transport = SSETransport(send_func=mock_send)
    emitter = AGUIEmitter(transport=transport)

    await emitter.emit(TextMessageContentEvent(message_id="m1", delta="Hello"))

    assert len(sent) == 1
    assert sent[0].startswith("event: TEXT_MESSAGE_CONTENT\n")
    assert "data: " in sent[0]
    assert sent[0].endswith("\n\n")

    # Verify the data line is valid JSON
    data_line = sent[0].split("data: ")[1].strip()
    parsed = json.loads(data_line)
    assert parsed["messageId"] == "m1"
    assert parsed["delta"] == "Hello"


@pytest.mark.asyncio
async def test_websocket_transport():
    """WebSocketTransport sends JSON messages."""
    sent: list[str] = []

    async def mock_send(data: str) -> None:
        sent.append(data)

    transport = WebSocketTransport(send_func=mock_send)
    emitter = AGUIEmitter(transport=transport)

    await emitter.emit(StepStartedEvent(step_name="draft"))

    assert len(sent) == 1
    parsed = json.loads(sent[0])
    assert parsed["type"] == "STEP_STARTED"
    assert parsed["stepName"] == "draft"


@pytest.mark.asyncio
async def test_multiple_transports():
    """Emitter broadcasts to all registered transports."""
    cb1 = CallbackTransport()
    cb2 = CallbackTransport()
    emitter = AGUIEmitter(transport=[cb1, cb2])

    await emitter.emit(RunStartedEvent(thread_id="t1", run_id="r1"))

    assert len(cb1.events) == 1
    assert len(cb2.events) == 1


@pytest.mark.asyncio
async def test_add_transport():
    """Transports can be added after construction."""
    emitter = AGUIEmitter()
    cb = CallbackTransport()
    emitter.add_transport(cb)

    await emitter.emit(RunStartedEvent(thread_id="t1", run_id="r1"))
    assert len(cb.events) == 1


@pytest.mark.asyncio
async def test_emitter_no_transport():
    """Emitter with no transports silently drops events."""
    emitter = AGUIEmitter()
    await emitter.emit(RunStartedEvent(thread_id="t1", run_id="r1"))
    # No error raised


@pytest.mark.asyncio
async def test_emitter_handles_transport_error():
    """Emitter continues if a transport raises an error."""
    cb = CallbackTransport()

    async def failing_send(data: str) -> None:
        raise ConnectionError("Connection lost")

    failing = SSETransport(send_func=failing_send)
    emitter = AGUIEmitter(transport=[failing, cb])

    await emitter.emit(RunStartedEvent(thread_id="t1", run_id="r1"))

    # Failing transport is skipped, callback still receives
    assert len(cb.events) == 1


@pytest.mark.asyncio
async def test_emitter_close():
    """Close calls close on all transports."""
    cb = CallbackTransport()
    emitter = AGUIEmitter(transport=cb)
    await emitter.close()  # Should not raise


# ------------------------------------------------------------------
# Full event flow simulation
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_event_flow():
    """Simulate a complete agent run event sequence."""
    cb = CallbackTransport()
    emitter = AGUIEmitter(transport=cb)

    # Run lifecycle
    await emitter.emit(RunStartedEvent(thread_id="t1", run_id="r1"))
    await emitter.emit(StepStartedEvent(step_name="llm_call"))

    # Text streaming
    await emitter.emit(TextMessageStartEvent(message_id="m1"))
    await emitter.emit(TextMessageContentEvent(message_id="m1", delta="Let me "))
    await emitter.emit(TextMessageContentEvent(message_id="m1", delta="search..."))
    await emitter.emit(TextMessageEndEvent(message_id="m1"))

    # Tool call
    await emitter.emit(ToolCallStartEvent(tool_call_id="tc1", tool_call_name="search"))
    await emitter.emit(ToolCallArgsEvent(tool_call_id="tc1", delta='{"q":"AI"}'))
    await emitter.emit(ToolCallEndEvent(tool_call_id="tc1"))
    await emitter.emit(
        ToolCallResultEvent(message_id="m2", tool_call_id="tc1", content="Found 5 results")
    )

    # Final response
    await emitter.emit(TextMessageStartEvent(message_id="m3"))
    await emitter.emit(TextMessageContentEvent(message_id="m3", delta="Here are the results."))
    await emitter.emit(TextMessageEndEvent(message_id="m3"))

    await emitter.emit(StepFinishedEvent(step_name="llm_call"))
    await emitter.emit(RunFinishedEvent(thread_id="t1", run_id="r1", result="done"))

    assert len(cb.events) == 15
    types = [e.type for e in cb.events]
    assert types[0] == EventType.RUN_STARTED
    assert types[-1] == EventType.RUN_FINISHED
