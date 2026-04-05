# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""AG-UI event types — 16 Pydantic models following the AG-UI specification.

Event categories:
- **Lifecycle**: RUN_STARTED, RUN_FINISHED, RUN_ERROR, STEP_STARTED, STEP_FINISHED
- **Text Message**: TEXT_MESSAGE_START, TEXT_MESSAGE_CONTENT, TEXT_MESSAGE_END
- **Tool Call**: TOOL_CALL_START, TOOL_CALL_ARGS, TOOL_CALL_END, TOOL_CALL_RESULT
- **State**: STATE_SNAPSHOT, STATE_DELTA (RFC 6902 JSON Patch), MESSAGES_SNAPSHOT
- **Special**: RAW, CUSTOM

Reference: https://docs.ag-ui.com/concepts/events
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """All AG-UI event type identifiers."""

    # Lifecycle
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"

    # Text Message
    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"

    # Tool Call
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"

    # State
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    MESSAGES_SNAPSHOT = "MESSAGES_SNAPSHOT"

    # Special
    RAW = "RAW"
    CUSTOM = "CUSTOM"


class BaseEvent(BaseModel):
    """Base class for all AG-UI events."""

    type: EventType
    timestamp: float = Field(default_factory=time.time)
    raw_event: Any | None = Field(default=None, alias="rawEvent")

    model_config = {"populate_by_name": True}


# ------------------------------------------------------------------
# Lifecycle events
# ------------------------------------------------------------------


class RunStartedEvent(BaseEvent):
    """Emitted when an agent run begins."""

    type: Literal[EventType.RUN_STARTED] = EventType.RUN_STARTED
    thread_id: str = Field(alias="threadId")
    run_id: str = Field(alias="runId")
    parent_run_id: str | None = Field(default=None, alias="parentRunId")
    input: Any | None = None


class RunFinishedEvent(BaseEvent):
    """Emitted when an agent run completes successfully."""

    type: Literal[EventType.RUN_FINISHED] = EventType.RUN_FINISHED
    thread_id: str = Field(alias="threadId")
    run_id: str = Field(alias="runId")
    result: Any | None = None


class RunErrorEvent(BaseEvent):
    """Emitted when an agent run fails."""

    type: Literal[EventType.RUN_ERROR] = EventType.RUN_ERROR
    message: str
    code: str | None = None


class StepStartedEvent(BaseEvent):
    """Emitted when a named step within a run begins."""

    type: Literal[EventType.STEP_STARTED] = EventType.STEP_STARTED
    step_name: str = Field(alias="stepName")


class StepFinishedEvent(BaseEvent):
    """Emitted when a named step within a run completes."""

    type: Literal[EventType.STEP_FINISHED] = EventType.STEP_FINISHED
    step_name: str = Field(alias="stepName")


# ------------------------------------------------------------------
# Text message events
# ------------------------------------------------------------------


class TextMessageStartEvent(BaseEvent):
    """Emitted when the agent begins a new text message."""

    type: Literal[EventType.TEXT_MESSAGE_START] = EventType.TEXT_MESSAGE_START
    message_id: str = Field(alias="messageId")
    role: Literal["developer", "system", "assistant", "user", "tool"] = "assistant"


class TextMessageContentEvent(BaseEvent):
    """Emitted for each text chunk within a message."""

    type: Literal[EventType.TEXT_MESSAGE_CONTENT] = EventType.TEXT_MESSAGE_CONTENT
    message_id: str = Field(alias="messageId")
    delta: str


class TextMessageEndEvent(BaseEvent):
    """Emitted when a text message is complete."""

    type: Literal[EventType.TEXT_MESSAGE_END] = EventType.TEXT_MESSAGE_END
    message_id: str = Field(alias="messageId")


# ------------------------------------------------------------------
# Tool call events
# ------------------------------------------------------------------


class ToolCallStartEvent(BaseEvent):
    """Emitted when the agent initiates a tool call."""

    type: Literal[EventType.TOOL_CALL_START] = EventType.TOOL_CALL_START
    tool_call_id: str = Field(alias="toolCallId")
    tool_call_name: str = Field(alias="toolCallName")
    parent_message_id: str | None = Field(default=None, alias="parentMessageId")


class ToolCallArgsEvent(BaseEvent):
    """Emitted for streamed tool call argument fragments."""

    type: Literal[EventType.TOOL_CALL_ARGS] = EventType.TOOL_CALL_ARGS
    tool_call_id: str = Field(alias="toolCallId")
    delta: str


class ToolCallEndEvent(BaseEvent):
    """Emitted when a tool call is complete (before result)."""

    type: Literal[EventType.TOOL_CALL_END] = EventType.TOOL_CALL_END
    tool_call_id: str = Field(alias="toolCallId")


class ToolCallResultEvent(BaseEvent):
    """Emitted with the result of a tool execution."""

    type: Literal[EventType.TOOL_CALL_RESULT] = EventType.TOOL_CALL_RESULT
    message_id: str = Field(alias="messageId")
    tool_call_id: str = Field(alias="toolCallId")
    content: Any
    role: str = "tool"


# ------------------------------------------------------------------
# State events
# ------------------------------------------------------------------


class StateSnapshotEvent(BaseEvent):
    """Emitted with a complete state snapshot."""

    type: Literal[EventType.STATE_SNAPSHOT] = EventType.STATE_SNAPSHOT
    snapshot: Any


class JSONPatchOperation(BaseModel):
    """A single RFC 6902 JSON Patch operation."""

    op: Literal["add", "remove", "replace", "move", "copy", "test"]
    path: str
    value: Any | None = None
    from_path: str | None = Field(default=None, alias="from")

    model_config = {"populate_by_name": True}


class StateDeltaEvent(BaseEvent):
    """Emitted with an incremental state update (RFC 6902 JSON Patch)."""

    type: Literal[EventType.STATE_DELTA] = EventType.STATE_DELTA
    delta: list[JSONPatchOperation]


class MessagesSnapshotEvent(BaseEvent):
    """Emitted with the full conversation message history."""

    type: Literal[EventType.MESSAGES_SNAPSHOT] = EventType.MESSAGES_SNAPSHOT
    messages: list[dict[str, Any]]


# ------------------------------------------------------------------
# Special events
# ------------------------------------------------------------------


class RawEvent(BaseEvent):
    """Wraps a raw event from an external system."""

    type: Literal[EventType.RAW] = EventType.RAW
    event: Any
    source: str | None = None


class CustomEvent(BaseEvent):
    """Application-defined custom event."""

    type: Literal[EventType.CUSTOM] = EventType.CUSTOM
    name: str
    value: Any
