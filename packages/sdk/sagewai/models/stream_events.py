# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Structured streaming events for agent execution.

Typed event objects emitted during agent runs, providing structured
feedback about the agent's lifecycle. These complement the existing
:class:`AgentEvent` enum (which identifies WHAT happened) with rich
data objects (which describe the details).

Usage::

    from sagewai.models.stream_events import (
        MessageStart, MessageDelta, ToolCallStart, ToolCallEnd,
        CompactionTriggered, BudgetWarning, MessageStop, StopReason,
    )

    agent.on_event(lambda event, data: print(event, data))
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any


class StopReason(enum.Enum):
    """Why the agent stopped generating."""

    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class MessageStart:
    """Emitted when the agent begins generating a response."""

    session_id: str = ""
    model: str = ""
    timestamp: float = field(default_factory=time.time)
    agent_name: str = ""


@dataclass
class MessageDelta:
    """Emitted for each text chunk during streaming."""

    delta: str = ""
    message_id: str = ""


@dataclass
class ToolCallStart:
    """Emitted when a tool call begins."""

    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""


@dataclass
class ToolCallEnd:
    """Emitted when a tool call completes."""

    tool_name: str = ""
    tool_call_id: str = ""
    result: str = ""
    error: str = ""
    duration_ms: float = 0.0


@dataclass
class PermissionDenial:
    """Emitted when a tool call is denied by permission policy."""

    tool_name: str = ""
    required_level: str = ""
    current_level: str = ""
    reason: str = ""


@dataclass
class CompactionTriggered:
    """Emitted when context compaction runs."""

    strategy: str = ""
    tokens_before: int = 0
    tokens_after: int = 0
    messages_before: int = 0
    messages_after: int = 0


@dataclass
class BudgetWarning:
    """Emitted when spending approaches or exceeds budget."""

    current_spend: float = 0.0
    limit: float = 0.0
    action: str = ""  # "warn", "throttle", "stop"
    agent_name: str = ""


@dataclass
class UsageStats:
    """Token and cost usage from an agent run."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    duration_ms: float = 0.0


@dataclass
class MessageStop:
    """Emitted when the agent finishes generating."""

    stop_reason: StopReason = StopReason.COMPLETED
    usage: UsageStats | None = None
    agent_name: str = ""
    timestamp: float = field(default_factory=time.time)
