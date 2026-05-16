# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for self-correction loop — error recovery with failure exemplars."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.core.events import AgentEvent
from sagewai.core.self_correction import (
    CorrectionError,
    ExemplarStore,
    FailureExemplar,
    OutputValidator,
    SelfCorrectionStrategy,
    validate_json_output,
    validate_schema,
)
from sagewai.models.message import ChatMessage

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class TestValidateJson:
    def test_valid_json(self):
        result = validate_json_output('{"key": "value"}')
        assert result == {"key": "value"}

    def test_invalid_json(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            validate_json_output("not json")

    def test_none_input(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            validate_json_output(None)

    def test_validate_json_output_with_prose_preamble(self):
        """Validation must survive an SLM prose preamble before the fence."""
        raw = 'Sure, here is the result:\n```json\n{"ok": true}\n```'
        assert validate_json_output(raw) == {"ok": True}


class TestValidateSchema:
    def test_all_fields_present(self):
        validate_schema({"name": "Alice", "age": 30}, ["name", "age"])

    def test_missing_fields(self):
        with pytest.raises(ValueError, match="Missing required fields"):
            validate_schema({"name": "Alice"}, ["name", "age"])

    def test_empty_required(self):
        validate_schema({"anything": True}, [])


# ---------------------------------------------------------------------------
# OutputValidator
# ---------------------------------------------------------------------------


class TestOutputValidator:
    def test_no_validators(self):
        v = OutputValidator()
        assert not v.has_validators
        assert v.validate("anything") == []

    def test_json_validator_valid(self):
        v = OutputValidator()
        v.add_json_validator()
        assert v.validate('{"ok": true}') == []

    def test_json_validator_invalid(self):
        v = OutputValidator()
        v.add_json_validator()
        errors = v.validate("not json")
        assert len(errors) == 1
        assert "Invalid JSON" in errors[0]

    def test_json_validator_with_required_fields(self):
        v = OutputValidator()
        v.add_json_validator(["name", "status"])
        assert v.validate('{"name": "test", "status": "ok"}') == []
        errors = v.validate('{"name": "test"}')
        assert len(errors) == 1
        assert "status" in errors[0]

    def test_custom_validator(self):
        def must_start_with_hello(text: str) -> None:
            if not text.startswith("Hello"):
                raise ValueError("Must start with Hello")

        v = OutputValidator()
        v.add_custom_validator("greeting", must_start_with_hello)
        assert v.validate("Hello world") == []
        errors = v.validate("Goodbye")
        assert len(errors) == 1

    def test_multiple_validators(self):
        v = OutputValidator()
        v.add_json_validator(["name"])
        v.add_custom_validator(
            "length",
            lambda t: None if len(t) > 5 else (_ for _ in ()).throw(ValueError("too short")),
        )
        # Valid JSON but might fail custom
        errors = v.validate('{"name": "x"}')
        assert errors == []


# ---------------------------------------------------------------------------
# ExemplarStore
# ---------------------------------------------------------------------------


class TestExemplarStore:
    def test_add_and_find(self):
        store = ExemplarStore()
        ex = FailureExemplar(
            error_type="json_error",
            bad_output="not json",
            correction_prompt="Fix the JSON",
            corrected_output='{"fixed": true}',
        )
        store.add(ex)
        assert store.find("json_error") is ex
        assert store.count == 1

    def test_find_missing(self):
        store = ExemplarStore()
        assert store.find("missing") is None

    def test_find_best_substring(self):
        store = ExemplarStore()
        ex = FailureExemplar(
            error_type="schema",
            bad_output="{}",
            correction_prompt="Add fields",
            corrected_output='{"name": "x"}',
        )
        store.add(ex)
        # Should match because "schema" appears in the error message
        assert store.find_best("Missing schema fields") is ex

    def test_find_best_no_match(self):
        store = ExemplarStore()
        ex = FailureExemplar(
            error_type="schema",
            bad_output="{}",
            correction_prompt="Fix",
            corrected_output="{}",
        )
        store.add(ex)
        assert store.find_best("totally unrelated error") is None

    def test_clear(self):
        store = ExemplarStore()
        store.add(
            FailureExemplar(
                error_type="test", bad_output="", correction_prompt="", corrected_output=""
            )
        )
        store.clear()
        assert store.count == 0

    def test_multiple_exemplars_same_type(self):
        store = ExemplarStore()
        ex1 = FailureExemplar(
            error_type="json", bad_output="a", correction_prompt="", corrected_output=""
        )
        ex2 = FailureExemplar(
            error_type="json", bad_output="b", correction_prompt="", corrected_output=""
        )
        store.add(ex1)
        store.add(ex2)
        assert store.count == 2
        # find returns most recent
        assert store.find("json") is ex2


# ---------------------------------------------------------------------------
# SelfCorrectionStrategy
# ---------------------------------------------------------------------------


def _make_mock_agent():
    """Create a mock agent with required methods."""
    agent = MagicMock()
    agent.config = MagicMock()
    agent.config.name = "test-agent"
    agent._emit = AsyncMock()
    return agent


def _make_mock_strategy(responses: list[ChatMessage]):
    """Create a mock base strategy that returns responses in sequence."""
    strategy = MagicMock()
    strategy.execute = AsyncMock(side_effect=responses)
    return strategy


class TestSelfCorrectionStrategy:
    @pytest.mark.asyncio
    async def test_no_validator_passes_through(self):
        """Without a validator, the base strategy result is returned directly."""
        response = ChatMessage.assistant("Hello")
        base = _make_mock_strategy([response])
        strategy = SelfCorrectionStrategy(base_strategy=base)
        agent = _make_mock_agent()

        result = await strategy.execute(agent, [], [], 10)
        assert result.content == "Hello"
        base.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_valid_output_passes(self):
        """Valid output passes validation without correction."""
        response = ChatMessage.assistant('{"name": "test"}')
        base = _make_mock_strategy([response])
        validator = OutputValidator()
        validator.add_json_validator(["name"])

        strategy = SelfCorrectionStrategy(base_strategy=base, validator=validator)
        agent = _make_mock_agent()

        result = await strategy.execute(agent, [], [], 10)
        assert result.content == '{"name": "test"}'

    @pytest.mark.asyncio
    async def test_correction_on_invalid_output(self):
        """Invalid output triggers correction and succeeds on retry."""
        bad_response = ChatMessage.assistant("not json")
        good_response = ChatMessage.assistant('{"name": "fixed"}')
        base = _make_mock_strategy([bad_response, good_response])
        validator = OutputValidator()
        validator.add_json_validator(["name"])

        strategy = SelfCorrectionStrategy(
            base_strategy=base, validator=validator, max_corrections=2
        )
        agent = _make_mock_agent()

        result = await strategy.execute(agent, [], [], 10)
        assert result.content == '{"name": "fixed"}'
        assert base.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_max_corrections_exceeded(self):
        """CorrectionError raised when max corrections are exhausted."""
        bad = ChatMessage.assistant("bad output")
        base = _make_mock_strategy([bad, bad, bad])
        validator = OutputValidator()
        validator.add_json_validator()

        strategy = SelfCorrectionStrategy(
            base_strategy=base, validator=validator, max_corrections=2
        )
        agent = _make_mock_agent()

        with pytest.raises(CorrectionError) as exc_info:
            await strategy.execute(agent, [], [], 10)
        assert exc_info.value.attempts == 2

    @pytest.mark.asyncio
    async def test_correction_prompt_includes_error(self):
        """Correction adds a user message with the error description."""
        bad_response = ChatMessage.assistant("not json")
        good_response = ChatMessage.assistant('{"ok": true}')
        base = _make_mock_strategy([bad_response, good_response])
        validator = OutputValidator()
        validator.add_json_validator()

        strategy = SelfCorrectionStrategy(base_strategy=base, validator=validator)
        agent = _make_mock_agent()
        messages: list[ChatMessage] = []

        await strategy.execute(agent, messages, [], 10)
        # Messages should contain the correction prompt
        user_msgs = [m for m in messages if m.role.value == "user"]
        assert len(user_msgs) == 1
        assert "error" in user_msgs[0].content.lower()

    @pytest.mark.asyncio
    async def test_exemplar_included_in_correction(self):
        """When an exemplar matches, it's included in the correction prompt."""
        store = ExemplarStore()
        store.add(
            FailureExemplar(
                error_type="Invalid JSON",
                bad_output="broken",
                correction_prompt="Fix JSON",
                corrected_output='{"fixed": true}',
            )
        )

        bad_response = ChatMessage.assistant("broken")
        good_response = ChatMessage.assistant('{"ok": true}')
        base = _make_mock_strategy([bad_response, good_response])
        validator = OutputValidator()
        validator.add_json_validator()

        strategy = SelfCorrectionStrategy(
            base_strategy=base, validator=validator, exemplar_store=store
        )
        agent = _make_mock_agent()
        messages: list[ChatMessage] = []

        await strategy.execute(agent, messages, [], 10)
        user_msgs = [m for m in messages if m.role.value == "user"]
        assert '{"fixed": true}' in user_msgs[0].content

    @pytest.mark.asyncio
    async def test_emits_events(self):
        """Self-correction emits step events."""
        bad_response = ChatMessage.assistant("bad")
        good_response = ChatMessage.assistant('{"ok": true}')
        base = _make_mock_strategy([bad_response, good_response])
        validator = OutputValidator()
        validator.add_json_validator()

        strategy = SelfCorrectionStrategy(base_strategy=base, validator=validator)
        agent = _make_mock_agent()

        await strategy.execute(agent, [], [], 10)
        # Should have emitted STEP_STARTED and STEP_FINISHED for correction
        step_calls = [
            c
            for c in agent._emit.call_args_list
            if c[0][0] in (AgentEvent.STEP_STARTED, AgentEvent.STEP_FINISHED)
        ]
        assert len(step_calls) >= 2

    @pytest.mark.asyncio
    async def test_max_corrections_clamped(self):
        """max_corrections is clamped to at least 1."""
        strategy = SelfCorrectionStrategy(max_corrections=0)
        assert strategy.max_corrections == 1


# ---------------------------------------------------------------------------
# CorrectionError
# ---------------------------------------------------------------------------


class TestCorrectionError:
    def test_attributes(self):
        err = CorrectionError("bad json", 3)
        assert err.original_error == "bad json"
        assert err.attempts == 3
        assert "3 attempts" in str(err)

    def test_inherits_exception(self):
        assert issubclass(CorrectionError, Exception)
