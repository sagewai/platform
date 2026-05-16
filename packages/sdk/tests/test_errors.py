# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the unified error hierarchy in sagewai.errors."""

from __future__ import annotations

import pytest

from sagewai.errors import (
    SagewaiAuthError,
    SagewaiConfigError,
    SagewaiContextLengthError,
    SagewaiError,
    SagewaiLLMError,
    SagewaiModelNotFoundError,
    SagewaiRateLimitError,
    SagewaiTimeoutError,
    SagewaiToolError,
    SagewaiWorkflowError,
)


class TestHierarchy:
    """Verify the class hierarchy is correct."""

    def test_llm_error_is_sagewai_error(self) -> None:
        assert issubclass(SagewaiLLMError, SagewaiError)

    def test_rate_limit_is_llm_error(self) -> None:
        assert issubclass(SagewaiRateLimitError, SagewaiLLMError)

    def test_auth_error_is_llm_error(self) -> None:
        assert issubclass(SagewaiAuthError, SagewaiLLMError)

    def test_model_not_found_is_llm_error(self) -> None:
        assert issubclass(SagewaiModelNotFoundError, SagewaiLLMError)

    def test_context_length_is_llm_error(self) -> None:
        assert issubclass(SagewaiContextLengthError, SagewaiLLMError)

    def test_timeout_is_sagewai_error(self) -> None:
        assert issubclass(SagewaiTimeoutError, SagewaiError)

    def test_config_is_sagewai_error(self) -> None:
        assert issubclass(SagewaiConfigError, SagewaiError)

    def test_workflow_is_sagewai_error(self) -> None:
        assert issubclass(SagewaiWorkflowError, SagewaiError)

    def test_tool_is_sagewai_error(self) -> None:
        assert issubclass(SagewaiToolError, SagewaiError)


class TestStructuredAttrs:
    """Verify structured attributes on LLM errors."""

    def test_llm_error_attrs(self) -> None:
        err = SagewaiLLMError(
            "boom", provider="openai", model="gpt-4o", agent_name="helper",
        )
        assert err.provider == "openai"
        assert err.model == "gpt-4o"
        assert err.agent_name == "helper"
        assert str(err) == "boom"

    def test_llm_error_defaults(self) -> None:
        err = SagewaiLLMError("fail")
        assert err.provider == ""
        assert err.model == ""
        assert err.agent_name == ""

    def test_rate_limit_retry_after(self) -> None:
        err = SagewaiRateLimitError(
            "rate limited",
            provider="anthropic",
            model="claude-3",
            retry_after=30.0,
        )
        assert err.retry_after == 30.0
        assert err.provider == "anthropic"

    def test_rate_limit_retry_after_none(self) -> None:
        err = SagewaiRateLimitError("rate limited")
        assert err.retry_after is None


class TestCausePreservation:
    """Verify __cause__ is preserved when wrapping."""

    def test_cause_chain(self) -> None:
        original = ValueError("upstream failure")
        try:
            raise SagewaiLLMError("wrapped") from original
        except SagewaiLLMError as exc:
            assert exc.__cause__ is original

    def test_nested_cause(self) -> None:
        root = ConnectionError("network down")
        mid = SagewaiLLMError("provider failed")
        mid.__cause__ = root
        try:
            raise SagewaiRateLimitError("rate limited") from mid
        except SagewaiRateLimitError as exc:
            assert exc.__cause__ is mid
            assert exc.__cause__.__cause__ is root


class TestReparenting:
    """Verify domain errors are re-parented under the unified hierarchy."""

    def test_step_timeout_is_sagewai_timeout(self) -> None:
        from sagewai.core.durability import StepTimeoutError

        assert issubclass(StepTimeoutError, SagewaiTimeoutError)

    def test_workflow_step_error_is_sagewai_workflow(self) -> None:
        from sagewai.core.state import WorkflowStepError

        assert issubclass(WorkflowStepError, SagewaiWorkflowError)

    def test_queue_full_is_sagewai_workflow(self) -> None:
        from sagewai.core.state import QueueFullError

        assert issubclass(QueueFullError, SagewaiWorkflowError)


class TestBroadCatch:
    """Verify a broad ``except SagewaiError`` catches all subtypes."""

    @pytest.mark.parametrize(
        "exc_class",
        [
            SagewaiLLMError,
            SagewaiRateLimitError,
            SagewaiAuthError,
            SagewaiModelNotFoundError,
            SagewaiContextLengthError,
            SagewaiTimeoutError,
            SagewaiConfigError,
            SagewaiWorkflowError,
            SagewaiToolError,
        ],
    )
    def test_broad_catch(self, exc_class: type[SagewaiError]) -> None:
        with pytest.raises(SagewaiError):
            raise exc_class("test")
