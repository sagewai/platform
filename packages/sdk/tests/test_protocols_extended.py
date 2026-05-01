# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Extended tests for AG-UI and A2A protocols — edge cases and integration."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from sagewai.protocols.agui.emitter import (
    AGUIEmitter,
    CallbackTransport,
    SSETransport,
    WebSocketTransport,
)
from sagewai.protocols.agui.events import (
    BaseEvent,
    CustomEvent,
    EventType,
    JSONPatchOperation,
    MessagesSnapshotEvent,
    RawEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
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
)


class TestEventTypeEnum:
    """Test EventType enum completeness and values."""

    def test_all_17_event_types(self) -> None:
        assert len(EventType) == 17

    def test_lifecycle_events(self) -> None:
        assert EventType.RUN_STARTED.value == "RUN_STARTED"
        assert EventType.RUN_FINISHED.value == "RUN_FINISHED"
        assert EventType.RUN_ERROR.value == "RUN_ERROR"
        assert EventType.STEP_STARTED.value == "STEP_STARTED"
        assert EventType.STEP_FINISHED.value == "STEP_FINISHED"

    def test_text_events(self) -> None:
        assert EventType.TEXT_MESSAGE_START.value == "TEXT_MESSAGE_START"
        assert EventType.TEXT_MESSAGE_CONTENT.value == "TEXT_MESSAGE_CONTENT"
        assert EventType.TEXT_MESSAGE_END.value == "TEXT_MESSAGE_END"

    def test_tool_events(self) -> None:
        assert EventType.TOOL_CALL_START.value == "TOOL_CALL_START"
        assert EventType.TOOL_CALL_ARGS.value == "TOOL_CALL_ARGS"
        assert EventType.TOOL_CALL_END.value == "TOOL_CALL_END"
        assert EventType.TOOL_CALL_RESULT.value == "TOOL_CALL_RESULT"

    def test_state_events(self) -> None:
        assert EventType.STATE_SNAPSHOT.value == "STATE_SNAPSHOT"
        assert EventType.STATE_DELTA.value == "STATE_DELTA"
        assert EventType.MESSAGES_SNAPSHOT.value == "MESSAGES_SNAPSHOT"

    def test_special_events(self) -> None:
        assert EventType.RAW.value == "RAW"
        assert EventType.CUSTOM.value == "CUSTOM"


class TestBaseEventTimestamp:
    """Test auto-generated timestamps on events."""

    def test_timestamp_auto_generated(self) -> None:
        e1 = RunStartedEvent(thread_id="t1", run_id="r1")
        e2 = RunStartedEvent(thread_id="t1", run_id="r2")
        assert e1.timestamp > 0
        assert e2.timestamp >= e1.timestamp

    def test_event_type_set_correctly(self) -> None:
        e = RunStartedEvent(thread_id="t1", run_id="r1")
        assert e.type == EventType.RUN_STARTED

        e2 = RunErrorEvent(message="fail", code="ERR_500")
        assert e2.type == EventType.RUN_ERROR


class TestJSONPatchOperation:
    """Test RFC 6902 JSON Patch operations."""

    def test_add_operation(self) -> None:
        op = JSONPatchOperation(op="add", path="/foo", value="bar")
        data = op.model_dump(by_alias=True)
        assert data["op"] == "add"
        assert data["path"] == "/foo"
        assert data["value"] == "bar"

    def test_move_operation_with_from(self) -> None:
        op = JSONPatchOperation(op="move", path="/new", from_path="/old")
        data = op.model_dump(by_alias=True)
        assert data["op"] == "move"
        assert data["from"] == "/old"  # aliased

    def test_remove_operation(self) -> None:
        op = JSONPatchOperation(op="remove", path="/obsolete")
        data = op.model_dump(by_alias=True)
        assert data["op"] == "remove"
        assert data["path"] == "/obsolete"


class TestStateDelta:
    """Test state delta with JSON Patch operations."""

    def test_state_delta_with_patches(self) -> None:
        delta = StateDeltaEvent(delta=[
            JSONPatchOperation(op="replace", path="/count", value=42),
            JSONPatchOperation(op="add", path="/new_field", value="hello"),
        ])
        assert delta.type == EventType.STATE_DELTA
        assert len(delta.delta) == 2


class TestTextMessageFlow:
    """Test the full text message lifecycle."""

    @pytest.mark.asyncio
    async def test_full_text_message_flow(self) -> None:
        transport = CallbackTransport()
        emitter = AGUIEmitter(transport=transport)

        await emitter.emit(TextMessageStartEvent(
            message_id="msg-1", role="assistant"
        ))
        await emitter.emit(TextMessageContentEvent(
            message_id="msg-1", delta="Hello"
        ))
        await emitter.emit(TextMessageContentEvent(
            message_id="msg-1", delta=" world!"
        ))
        await emitter.emit(TextMessageEndEvent(message_id="msg-1"))

        assert len(transport.events) == 4
        assert transport.events[0].type == EventType.TEXT_MESSAGE_START
        assert transport.events[1].type == EventType.TEXT_MESSAGE_CONTENT
        assert transport.events[3].type == EventType.TEXT_MESSAGE_END


class TestToolCallFlow:
    """Test the full tool call lifecycle."""

    @pytest.mark.asyncio
    async def test_full_tool_call_flow(self) -> None:
        transport = CallbackTransport()
        emitter = AGUIEmitter(transport=transport)

        await emitter.emit(ToolCallStartEvent(
            tool_call_id="tc-1",
            tool_call_name="search",
            parent_message_id="msg-1",
        ))
        await emitter.emit(ToolCallArgsEvent(
            tool_call_id="tc-1",
            delta='{"query": "test"}',
        ))
        await emitter.emit(ToolCallEndEvent(tool_call_id="tc-1"))
        await emitter.emit(ToolCallResultEvent(
            message_id="msg-2",
            tool_call_id="tc-1",
            content="Found 5 results",
        ))

        assert len(transport.events) == 4
        assert transport.events[0].type == EventType.TOOL_CALL_START
        assert transport.events[3].type == EventType.TOOL_CALL_RESULT


class TestSSETransport:
    """Test SSE transport formatting."""

    @pytest.mark.asyncio
    async def test_sse_format(self) -> None:
        sent: list[str] = []

        async def mock_send(data: str) -> None:
            sent.append(data)

        transport = SSETransport(send_func=mock_send)
        event = RunStartedEvent(thread_id="t1", run_id="r1")
        await transport.send(event)

        assert len(sent) == 1
        # SSE format: "event: TYPE\ndata: JSON\n\n"
        msg = sent[0]
        assert "event:" in msg or "data:" in msg


class TestWebSocketTransport:
    """Test WebSocket transport formatting."""

    @pytest.mark.asyncio
    async def test_websocket_json(self) -> None:
        sent: list[str] = []

        async def mock_send(data: str) -> None:
            sent.append(data)

        transport = WebSocketTransport(send_func=mock_send)
        event = StepStartedEvent(step_name="analyze")
        await transport.send(event)

        assert len(sent) == 1
        parsed = json.loads(sent[0])
        assert "type" in parsed


class TestEmitterResilience:
    """Test emitter behavior with failing transports."""

    @pytest.mark.asyncio
    async def test_failing_transport_doesnt_block_others(self) -> None:
        good = CallbackTransport()

        class BadTransport:
            async def send(self, event: BaseEvent) -> None:
                raise RuntimeError("Transport broken")

            async def close(self) -> None:
                pass

        emitter = AGUIEmitter(transport=[BadTransport(), good])
        await emitter.emit(RunStartedEvent(thread_id="t1", run_id="r1"))

        # Good transport should still receive the event
        assert len(good.events) == 1

    @pytest.mark.asyncio
    async def test_no_transports_silent(self) -> None:
        """Emitting with no transports should not raise."""
        emitter = AGUIEmitter()
        await emitter.emit(RunStartedEvent(thread_id="t1", run_id="r1"))

    @pytest.mark.asyncio
    async def test_close_with_failing_transport(self) -> None:
        """Close should handle transport close failures gracefully."""

        class FailClose:
            async def send(self, event: BaseEvent) -> None:
                pass

            async def close(self) -> None:
                raise RuntimeError("Close failed")

        emitter = AGUIEmitter(transport=FailClose())
        # Should not raise
        await emitter.close()


class TestCustomAndRawEvents:
    """Test special event types."""

    def test_custom_event(self) -> None:
        event = CustomEvent(name="my_metric", value={"accuracy": 0.95})
        assert event.type == EventType.CUSTOM
        assert event.name == "my_metric"
        assert event.value["accuracy"] == 0.95

    def test_raw_event(self) -> None:
        event = RawEvent(event={"raw": "data"}, source="external")
        assert event.type == EventType.RAW
        assert event.source == "external"

    def test_messages_snapshot(self) -> None:
        event = MessagesSnapshotEvent(messages=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ])
        assert event.type == EventType.MESSAGES_SNAPSHOT
        assert len(event.messages) == 2

    def test_state_snapshot(self) -> None:
        event = StateSnapshotEvent(snapshot={"count": 42, "items": [1, 2, 3]})
        assert event.type == EventType.STATE_SNAPSHOT
        assert event.snapshot["count"] == 42
