# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the Guardrails safety framework."""

from __future__ import annotations

from typing import Any

import pytest

from sagewai.core.base import BaseAgent
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec
from sagewai.safety.guardrails import (
    ContentFilter,
    GuardrailResult,
    GuardrailViolationError,
    OutputSchemaGuard,
    TokenBudgetGuard,
)


class SimpleAgent(BaseAgent):
    """Agent that returns whatever the test tells it to."""

    def __init__(self, response: str, **kwargs: Any):
        super().__init__(**kwargs)
        self._response = response

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        return ChatMessage.assistant(self._response)


# ---------------------------------------------------------------------------
# GuardrailResult
# ---------------------------------------------------------------------------


class TestGuardrailResult:
    def test_passed(self):
        r = GuardrailResult(passed=True)
        assert r.passed
        assert r.violation is None
        assert r.action == "block"

    def test_failed_with_violation(self):
        r = GuardrailResult(passed=False, violation="PII detected", action="block")
        assert not r.passed
        assert r.violation == "PII detected"


# ---------------------------------------------------------------------------
# ContentFilter
# ---------------------------------------------------------------------------


class TestContentFilter:
    @pytest.mark.asyncio
    async def test_clean_input_passes(self):
        guard = ContentFilter(blocklist=["password", "SSN"])
        result = await guard.check_input("What is the weather today?", {})
        assert result.passed

    @pytest.mark.asyncio
    async def test_blocked_word_in_input(self):
        guard = ContentFilter(blocklist=["password", "SSN"])
        result = await guard.check_input("My password is hunter2", {})
        assert not result.passed
        assert "password" in result.violation.lower()

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        guard = ContentFilter(blocklist=["secret"])
        result = await guard.check_input("This is a SECRET message", {})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_clean_output_passes(self):
        guard = ContentFilter(blocklist=["SSN"])
        result = await guard.check_output("The weather is sunny", {})
        assert result.passed

    @pytest.mark.asyncio
    async def test_blocked_word_in_output(self):
        guard = ContentFilter(blocklist=["SSN"])
        result = await guard.check_output("Your SSN is 123-45-6789", {})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_regex_patterns(self):
        guard = ContentFilter(patterns=[r"\d{3}-\d{2}-\d{4}"])  # SSN pattern
        result = await guard.check_output("Number: 123-45-6789", {})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_empty_blocklist_passes(self):
        guard = ContentFilter()
        result = await guard.check_input("anything", {})
        assert result.passed


# ---------------------------------------------------------------------------
# OutputSchemaGuard
# ---------------------------------------------------------------------------


class TestOutputSchemaGuard:
    @pytest.mark.asyncio
    async def test_valid_json_passes(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        guard = OutputSchemaGuard(schema=schema)
        result = await guard.check_output('{"name": "Alice"}', {})
        assert result.passed

    @pytest.mark.asyncio
    async def test_invalid_json_fails(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        guard = OutputSchemaGuard(schema=schema)
        result = await guard.check_output("not json", {})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_missing_required_field_fails(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        guard = OutputSchemaGuard(schema=schema)
        result = await guard.check_output('{"age": 30}', {})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_input_check_always_passes(self):
        schema = {"type": "object"}
        guard = OutputSchemaGuard(schema=schema)
        result = await guard.check_input("anything", {})
        assert result.passed


# ---------------------------------------------------------------------------
# TokenBudgetGuard
# ---------------------------------------------------------------------------


class TestTokenBudgetGuard:
    @pytest.mark.asyncio
    async def test_under_budget_passes(self):
        guard = TokenBudgetGuard(max_usd=1.0)
        result = await guard.check_input("hello", {"cost_usd_so_far": 0.5})
        assert result.passed

    @pytest.mark.asyncio
    async def test_over_budget_blocks(self):
        guard = TokenBudgetGuard(max_usd=1.0)
        result = await guard.check_input("hello", {"cost_usd_so_far": 1.5})
        assert not result.passed
        assert "budget" in result.violation.lower()

    @pytest.mark.asyncio
    async def test_no_context_passes(self):
        guard = TokenBudgetGuard(max_usd=1.0)
        result = await guard.check_input("hello", {})
        assert result.passed


# ---------------------------------------------------------------------------
# Integration with BaseAgent
# ---------------------------------------------------------------------------


class TestGuardrailIntegration:
    @pytest.mark.asyncio
    async def test_input_guardrail_blocks(self):
        agent = SimpleAgent(
            response="ok",
            name="guarded",
            guardrails=[ContentFilter(blocklist=["forbidden"])],
        )
        with pytest.raises(GuardrailViolationError, match="forbidden"):
            await agent.chat("This message has forbidden content")

    @pytest.mark.asyncio
    async def test_output_guardrail_blocks(self):
        agent = SimpleAgent(
            response="Here is your SSN: 123-45-6789",
            name="guarded",
            guardrails=[ContentFilter(patterns=[r"\d{3}-\d{2}-\d{4}"])],
        )
        with pytest.raises(GuardrailViolationError):
            await agent.chat("What is my SSN?")

    @pytest.mark.asyncio
    async def test_clean_message_passes(self):
        agent = SimpleAgent(
            response="The weather is sunny",
            name="guarded",
            guardrails=[ContentFilter(blocklist=["secret"])],
        )
        result = await agent.chat("What is the weather?")
        assert result == "The weather is sunny"

    @pytest.mark.asyncio
    async def test_multiple_guardrails(self):
        agent = SimpleAgent(
            response="ok",
            name="guarded",
            guardrails=[
                ContentFilter(blocklist=["blocked"]),
                TokenBudgetGuard(max_usd=0.01),
            ],
        )
        # First guardrail should catch it
        with pytest.raises(GuardrailViolationError):
            await agent.chat("This is blocked content")

    @pytest.mark.asyncio
    async def test_no_guardrails_works(self):
        agent = SimpleAgent(response="hello", name="unguarded")
        result = await agent.chat("hi")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_escalate_emits_event(self):
        guard = ContentFilter(blocklist=["risky"], action="escalate")
        agent = SimpleAgent(
            response="ok",
            name="guarded",
            guardrails=[guard],
        )

        events: list[tuple] = []
        agent.on_event(lambda event, data: events.append((event, data)))

        # Escalation should not raise, but emit event
        await agent.chat("This is risky content")

        from sagewai.core.events import AgentEvent

        escalation_events = [e for e, d in events if e == AgentEvent.GUARDRAIL_ESCALATION]
        assert len(escalation_events) >= 1


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestGuardrailEdgeCases:
    @pytest.mark.asyncio
    async def test_content_filter_empty_input(self):
        """Empty string should pass content filter."""
        f = ContentFilter(blocklist=["bad"])
        result = await f.check_input("", {})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_content_filter_unicode(self):
        """Content filter should handle unicode characters."""
        f = ContentFilter(blocklist=["verboten"])
        result = await f.check_input("Dies ist verboten!", {})
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_output_schema_guard_nested_object(self):
        """OutputSchemaGuard validates nested objects."""
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                }
            },
            "required": ["user"],
        }
        guard = OutputSchemaGuard(schema=schema)
        result = await guard.check_output('{"user": {"name": "Alice"}}', {})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_output_schema_guard_extra_fields_ok(self):
        """OutputSchemaGuard should allow extra fields by default."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        guard = OutputSchemaGuard(schema=schema)
        result = await guard.check_output('{"name": "Alice", "age": 30}', {})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_token_budget_guard_zero_budget(self):
        """Zero budget should block everything."""
        guard = TokenBudgetGuard(max_usd=0.0)
        result = await guard.check_input("anything", {"cost_usd_so_far": 0.001})
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_multiple_guardrails_first_blocks(self):
        """When multiple guardrails exist, first failing one blocks."""
        agent = SimpleAgent(
            response="clean",
            name="g",
            guardrails=[
                ContentFilter(blocklist=["input_bad"]),
                ContentFilter(blocklist=["other_bad"]),
            ],
        )
        with pytest.raises(GuardrailViolationError, match="input_bad"):
            await agent.chat("input_bad word")

    @pytest.mark.asyncio
    async def test_warn_action_does_not_raise(self):
        """Guardrail with action='warn' should not raise."""
        f = ContentFilter(blocklist=["risky"], action="warn")
        result = await f.check_input("risky content", {})
        assert result.passed is False
        assert result.action == "warn"
