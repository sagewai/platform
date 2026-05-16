# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the LLM Harness request classifier."""

from __future__ import annotations

import pytest

from sagewai.harness.classifier import (
    ClassificationResult,
    ClassifierThresholds,
    ComplexityTier,
    RequestClassifier,
)


@pytest.fixture
def classifier() -> RequestClassifier:
    return RequestClassifier()


class TestRequestClassifier:
    """Test the heuristic request classifier."""

    def test_simple_short_query(self, classifier: RequestClassifier) -> None:
        """Short, simple queries should classify as SIMPLE."""
        messages = [{"role": "user", "content": "fix typo"}]
        result = classifier.classify(messages)
        assert result.tier == ComplexityTier.SIMPLE
        assert result.score < 30

    def test_simple_autocomplete(self, classifier: RequestClassifier) -> None:
        """Quick autocomplete-style queries should be SIMPLE."""
        messages = [{"role": "user", "content": "add import os"}]
        result = classifier.classify(messages)
        assert result.tier == ComplexityTier.SIMPLE

    def test_medium_code_generation(self, classifier: RequestClassifier) -> None:
        """Moderate code generation should be MEDIUM."""
        messages = [
            {"role": "system", "content": "You are a helpful coding assistant. " * 30},
            {"role": "user", "content": (
                "Write a Python function that reads a CSV file, "
                "validates the email column using regex, and returns "
                "a list of invalid rows with their line numbers. "
                "Also implement proper error handling for missing files "
                "and malformed CSV data. Include type hints and docstrings."
            )},
        ]
        result = classifier.classify(messages)
        assert result.tier in (ComplexityTier.MEDIUM, ComplexityTier.COMPLEX)

    def test_complex_architecture_request(self, classifier: RequestClassifier) -> None:
        """Architecture/planning requests should classify as COMPLEX."""
        messages = [
            {"role": "system", "content": "You are a senior software architect. " * 100},
            {"role": "user", "content": (
                "Design and implement a multi-file migration system that "
                "restructures the entire authentication module. Plan the "
                "architecture for a microservices migration with proper "
                "security audit and end-to-end review of all components."
            )},
        ]
        result = classifier.classify(messages, tools=[
            {"type": "function", "function": {"name": f"tool_{i}"}} for i in range(8)
        ])
        assert result.tier == ComplexityTier.COMPLEX
        assert result.score >= 70

    def test_many_tools_increases_complexity(self, classifier: RequestClassifier) -> None:
        """Requests with many tools should score higher."""
        messages = [{"role": "user", "content": "Help me with this code"}]
        tools = [{"type": "function", "function": {"name": f"tool_{i}"}} for i in range(12)]

        result_no_tools = classifier.classify(messages)
        result_with_tools = classifier.classify(messages, tools=tools)

        assert result_with_tools.score > result_no_tools.score

    def test_long_conversation_increases_complexity(self, classifier: RequestClassifier) -> None:
        """Long conversations should score higher."""
        short_conv = [{"role": "user", "content": "Hello, help me with code."}]
        long_conv = []
        for i in range(15):
            long_conv.append({"role": "user", "content": f"Here's part {i} of my question."})
            long_conv.append({"role": "assistant", "content": f"Here's my response to part {i}."})

        result_short = classifier.classify(short_conv)
        result_long = classifier.classify(long_conv)

        assert result_long.score > result_short.score

    def test_large_system_prompt_increases_complexity(self, classifier: RequestClassifier) -> None:
        """Large system prompts indicate complex tasks."""
        small_sys = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Write some code."},
        ]
        large_sys = [
            {"role": "system", "content": "You are an expert. " * 500},
            {"role": "user", "content": "Write some code."},
        ]

        result_small = classifier.classify(small_sys)
        result_large = classifier.classify(large_sys)

        assert result_large.score > result_small.score

    def test_code_blocks_increase_complexity(self, classifier: RequestClassifier) -> None:
        """Multiple code blocks signal more complex requests."""
        no_code = [{"role": "user", "content": "Fix the bug in the login page."}]
        with_code = [{"role": "user", "content": (
            "Fix this bug:\n```python\ndef foo():\n    pass\n```\n"
            "And this:\n```python\ndef bar():\n    pass\n```\n"
            "And this:\n```python\ndef baz():\n    pass\n```"
        )}]

        result_no_code = classifier.classify(no_code)
        result_with_code = classifier.classify(with_code)

        assert result_with_code.score > result_no_code.score

    def test_complexity_keywords(self, classifier: RequestClassifier) -> None:
        """Complexity keywords should increase the score."""
        simple = [{"role": "user", "content": "fix this small typo in the readme"}]
        complex_msg = [{"role": "user", "content": (
            "architect a new security module and design the migration plan"
        )}]

        result_simple = classifier.classify(simple)
        result_complex = classifier.classify(complex_msg)

        assert result_complex.score > result_simple.score

    def test_custom_thresholds(self) -> None:
        """Custom thresholds should change tier boundaries."""
        # Very aggressive: almost everything is COMPLEX
        aggressive = RequestClassifier(
            thresholds=ClassifierThresholds(simple_max=10, complex_min=30)
        )
        # Use a message that scores around 30-50 (moderate length, no keywords)
        messages = [
            {"role": "user", "content": (
                "Help me implement a data processing pipeline that reads "
                "from multiple sources and aggregates the results."
            )},
        ]
        result = aggressive.classify(messages)
        # With aggressive thresholds (complex_min=30), score >= 30 → COMPLEX
        assert result.tier in (ComplexityTier.MEDIUM, ComplexityTier.COMPLEX)

    def test_classification_result_fields(self, classifier: RequestClassifier) -> None:
        """All result fields should be populated."""
        messages = [{"role": "user", "content": "Hello"}]
        result = classifier.classify(messages)

        assert isinstance(result, ClassificationResult)
        assert isinstance(result.tier, ComplexityTier)
        assert isinstance(result.score, int)
        assert 0.0 <= result.confidence <= 1.0
        assert len(result.reason) > 0
        assert "total_tokens" in result.signals
        assert "message_count" in result.signals

    def test_anthropic_content_array_format(self, classifier: RequestClassifier) -> None:
        """Should handle Anthropic's content array format."""
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "Fix this typo"},
            ]},
        ]
        result = classifier.classify(messages)
        assert result.tier == ComplexityTier.SIMPLE

    def test_empty_messages(self, classifier: RequestClassifier) -> None:
        """Should handle empty message list gracefully."""
        result = classifier.classify([])
        assert isinstance(result.tier, ComplexityTier)

    def test_score_clamped_0_100(self, classifier: RequestClassifier) -> None:
        """Score should always be between 0 and 100."""
        # Very simple
        simple = classifier.classify([{"role": "user", "content": "hi"}])
        assert 0 <= simple.score <= 100

        # Very complex
        complex_msg = classifier.classify(
            [{"role": "system", "content": "expert " * 2000},
             {"role": "user", "content": "architect and design " * 50}],
            tools=[{"type": "function", "function": {"name": f"t{i}"}} for i in range(20)],
        )
        assert 0 <= complex_msg.score <= 100


class TestComplexityTier:
    """Test the ComplexityTier enum."""

    def test_values(self) -> None:
        assert ComplexityTier.SIMPLE.value == "simple"
        assert ComplexityTier.MEDIUM.value == "medium"
        assert ComplexityTier.COMPLEX.value == "complex"

    def test_string_enum(self) -> None:
        assert str(ComplexityTier.SIMPLE) == "ComplexityTier.SIMPLE"
        assert ComplexityTier("simple") == ComplexityTier.SIMPLE
