# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""AG-UI protocol implementation — event types and emitter.

Implements the AG-UI event specification for real-time agent communication
with frontend clients via SSE or WebSocket transports.

Usage::

    from sagewai.protocols.agui import AGUIEmitter, RunStartedEvent

    emitter = AGUIEmitter()
    await emitter.emit(RunStartedEvent(thread_id="t1", run_id="r1"))
"""

from sagewai.protocols.agui.emitter import AGUIEmitter, SSETransport, WebSocketTransport
from sagewai.protocols.agui.events import (
    BaseEvent,
    CustomEvent,
    EventType,
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

__all__ = [
    "AGUIEmitter",
    "BaseEvent",
    "CustomEvent",
    "EventType",
    "MessagesSnapshotEvent",
    "RawEvent",
    "RunErrorEvent",
    "RunFinishedEvent",
    "RunStartedEvent",
    "SSETransport",
    "StateDeltaEvent",
    "StateSnapshotEvent",
    "StepFinishedEvent",
    "StepStartedEvent",
    "TextMessageContentEvent",
    "TextMessageEndEvent",
    "TextMessageStartEvent",
    "ToolCallArgsEvent",
    "ToolCallEndEvent",
    "ToolCallResultEvent",
    "ToolCallStartEvent",
    "WebSocketTransport",
]
