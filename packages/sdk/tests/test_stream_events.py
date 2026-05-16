# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for structured streaming event dataclasses."""

from __future__ import annotations

import dataclasses
import time

from sagewai.models.stream_events import (
    BudgetWarning,
    CompactionTriggered,
    MessageDelta,
    MessageStart,
    MessageStop,
    PermissionDenial,
    StopReason,
    ToolCallEnd,
    ToolCallStart,
    UsageStats,
)


class TestStopReason:
    """StopReason enum values."""

    def test_all_values(self) -> None:
        assert StopReason.COMPLETED.value == "completed"
        assert StopReason.MAX_TURNS.value == "max_turns"
        assert StopReason.BUDGET_EXCEEDED.value == "budget_exceeded"
        assert StopReason.CANCELLED.value == "cancelled"
        assert StopReason.ERROR.value == "error"

    def test_member_count(self) -> None:
        assert len(StopReason) == 5


class TestMessageStart:
    """MessageStart dataclass."""

    def test_defaults(self) -> None:
        evt = MessageStart()
        assert evt.session_id == ""
        assert evt.model == ""
        assert evt.agent_name == ""
        assert isinstance(evt.timestamp, float)

    def test_explicit_values(self) -> None:
        ts = 1700000000.0
        evt = MessageStart(
            session_id="s-1",
            model="gpt-4o",
            timestamp=ts,
            agent_name="Scout",
        )
        assert evt.session_id == "s-1"
        assert evt.model == "gpt-4o"
        assert evt.timestamp == ts
        assert evt.agent_name == "Scout"

    def test_timestamp_auto_set(self) -> None:
        before = time.time()
        evt = MessageStart()
        after = time.time()
        assert before <= evt.timestamp <= after


class TestMessageDelta:
    """MessageDelta dataclass."""

    def test_defaults(self) -> None:
        evt = MessageDelta()
        assert evt.delta == ""
        assert evt.message_id == ""

    def test_explicit_values(self) -> None:
        evt = MessageDelta(delta="Hello ", message_id="msg-42")
        assert evt.delta == "Hello "
        assert evt.message_id == "msg-42"


class TestToolCallStart:
    """ToolCallStart dataclass."""

    def test_defaults(self) -> None:
        evt = ToolCallStart()
        assert evt.tool_name == ""
        assert evt.arguments == {}
        assert evt.tool_call_id == ""

    def test_explicit_values(self) -> None:
        args = {"query": "test", "limit": 10}
        evt = ToolCallStart(
            tool_name="search",
            arguments=args,
            tool_call_id="tc-1",
        )
        assert evt.tool_name == "search"
        assert evt.arguments == args
        assert evt.tool_call_id == "tc-1"

    def test_arguments_not_shared(self) -> None:
        a = ToolCallStart()
        b = ToolCallStart()
        a.arguments["key"] = "val"
        assert "key" not in b.arguments


class TestToolCallEnd:
    """ToolCallEnd dataclass."""

    def test_defaults(self) -> None:
        evt = ToolCallEnd()
        assert evt.tool_name == ""
        assert evt.tool_call_id == ""
        assert evt.result == ""
        assert evt.error == ""
        assert evt.duration_ms == 0.0

    def test_explicit_values(self) -> None:
        evt = ToolCallEnd(
            tool_name="search",
            tool_call_id="tc-1",
            result='{"items": []}',
            error="",
            duration_ms=123.4,
        )
        assert evt.tool_name == "search"
        assert evt.duration_ms == 123.4
        assert evt.result == '{"items": []}'


class TestPermissionDenial:
    """PermissionDenial dataclass."""

    def test_defaults(self) -> None:
        evt = PermissionDenial()
        assert evt.tool_name == ""
        assert evt.required_level == ""
        assert evt.current_level == ""
        assert evt.reason == ""

    def test_explicit_values(self) -> None:
        evt = PermissionDenial(
            tool_name="delete_file",
            required_level="admin",
            current_level="user",
            reason="Insufficient permissions",
        )
        assert evt.tool_name == "delete_file"
        assert evt.required_level == "admin"
        assert evt.current_level == "user"
        assert evt.reason == "Insufficient permissions"


class TestCompactionTriggered:
    """CompactionTriggered dataclass."""

    def test_defaults(self) -> None:
        evt = CompactionTriggered()
        assert evt.strategy == ""
        assert evt.tokens_before == 0
        assert evt.tokens_after == 0
        assert evt.messages_before == 0
        assert evt.messages_after == 0

    def test_explicit_values(self) -> None:
        evt = CompactionTriggered(
            strategy="summarize",
            tokens_before=8000,
            tokens_after=2000,
            messages_before=50,
            messages_after=10,
        )
        assert evt.strategy == "summarize"
        assert evt.tokens_before == 8000
        assert evt.tokens_after == 2000
        assert evt.messages_before == 50
        assert evt.messages_after == 10


class TestBudgetWarning:
    """BudgetWarning dataclass."""

    def test_defaults(self) -> None:
        evt = BudgetWarning()
        assert evt.current_spend == 0.0
        assert evt.limit == 0.0
        assert evt.action == ""
        assert evt.agent_name == ""

    def test_explicit_values(self) -> None:
        evt = BudgetWarning(
            current_spend=4.50,
            limit=5.00,
            action="warn",
            agent_name="Auditor",
        )
        assert evt.current_spend == 4.50
        assert evt.limit == 5.00
        assert evt.action == "warn"
        assert evt.agent_name == "Auditor"


class TestUsageStats:
    """UsageStats dataclass."""

    def test_defaults(self) -> None:
        evt = UsageStats()
        assert evt.input_tokens == 0
        assert evt.output_tokens == 0
        assert evt.total_tokens == 0
        assert evt.cost_usd == 0.0
        assert evt.model == ""
        assert evt.duration_ms == 0.0

    def test_explicit_values(self) -> None:
        evt = UsageStats(
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            cost_usd=0.03,
            model="gpt-4o",
            duration_ms=2345.6,
        )
        assert evt.input_tokens == 1000
        assert evt.output_tokens == 500
        assert evt.total_tokens == 1500
        assert evt.cost_usd == 0.03
        assert evt.model == "gpt-4o"
        assert evt.duration_ms == 2345.6

    def test_serialization(self) -> None:
        evt = UsageStats(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost_usd=0.01,
            model="claude-3",
            duration_ms=500.0,
        )
        d = dataclasses.asdict(evt)
        assert d == {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cost_usd": 0.01,
            "model": "claude-3",
            "duration_ms": 500.0,
        }


class TestMessageStop:
    """MessageStop dataclass."""

    def test_defaults(self) -> None:
        evt = MessageStop()
        assert evt.stop_reason == StopReason.COMPLETED
        assert evt.usage is None
        assert evt.agent_name == ""
        assert isinstance(evt.timestamp, float)

    def test_explicit_values(self) -> None:
        usage = UsageStats(input_tokens=100, output_tokens=50, total_tokens=150)
        evt = MessageStop(
            stop_reason=StopReason.MAX_TURNS,
            usage=usage,
            agent_name="Planner",
            timestamp=1700000000.0,
        )
        assert evt.stop_reason == StopReason.MAX_TURNS
        assert evt.usage is usage
        assert evt.agent_name == "Planner"
        assert evt.timestamp == 1700000000.0

    def test_error_stop_reason(self) -> None:
        evt = MessageStop(stop_reason=StopReason.ERROR)
        assert evt.stop_reason == StopReason.ERROR

    def test_budget_exceeded_stop_reason(self) -> None:
        evt = MessageStop(stop_reason=StopReason.BUDGET_EXCEEDED)
        assert evt.stop_reason == StopReason.BUDGET_EXCEEDED


class TestSerialization:
    """Cross-cutting serialization tests."""

    def test_message_start_asdict(self) -> None:
        evt = MessageStart(session_id="s-1", model="gpt-4o", timestamp=1.0)
        d = dataclasses.asdict(evt)
        assert d["session_id"] == "s-1"
        assert d["model"] == "gpt-4o"
        assert d["timestamp"] == 1.0
        assert d["agent_name"] == ""

    def test_tool_call_start_asdict(self) -> None:
        evt = ToolCallStart(
            tool_name="search",
            arguments={"q": "test"},
            tool_call_id="tc-1",
        )
        d = dataclasses.asdict(evt)
        assert d["arguments"] == {"q": "test"}

    def test_message_stop_with_usage_asdict(self) -> None:
        usage = UsageStats(input_tokens=10, output_tokens=5, total_tokens=15)
        evt = MessageStop(
            stop_reason=StopReason.COMPLETED,
            usage=usage,
            timestamp=1.0,
        )
        d = dataclasses.asdict(evt)
        assert d["stop_reason"] == StopReason.COMPLETED
        assert d["usage"]["input_tokens"] == 10
        assert d["usage"]["total_tokens"] == 15

    def test_message_stop_none_usage_asdict(self) -> None:
        evt = MessageStop(timestamp=1.0)
        d = dataclasses.asdict(evt)
        assert d["usage"] is None

    def test_compaction_asdict(self) -> None:
        evt = CompactionTriggered(
            strategy="truncate",
            tokens_before=4000,
            tokens_after=1000,
        )
        d = dataclasses.asdict(evt)
        assert d["strategy"] == "truncate"
        assert d["tokens_before"] == 4000
        assert d["tokens_after"] == 1000

    def test_permission_denial_asdict(self) -> None:
        evt = PermissionDenial(
            tool_name="rm",
            required_level="admin",
            current_level="viewer",
            reason="Read-only access",
        )
        d = dataclasses.asdict(evt)
        assert d["tool_name"] == "rm"
        assert d["reason"] == "Read-only access"
