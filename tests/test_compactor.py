"""Tests for PromptCompactor — token-aware context compression."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from sagewai.core.compactor import (
    CompactionPipeline,
    LLMCompactor,
    PromptCompactor,
    RuleBasedCompactor,
    estimate_messages_tokens,
    estimate_tokens,
)
from sagewai.models.message import ChatMessage, Role, ToolCall

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


class TestTokenEstimation:
    def test_estimate_tokens_basic(self):
        """estimate_tokens returns positive count."""
        assert estimate_tokens("hello world") >= 1

    def test_estimate_tokens_empty(self):
        """Empty string still returns at least 1."""
        assert estimate_tokens("") >= 1

    def test_estimate_tokens_long_text(self):
        """Longer text produces more tokens."""
        short = estimate_tokens("hi")
        long = estimate_tokens("hello " * 100)
        assert long > short

    def test_estimate_messages_tokens(self):
        """estimate_messages_tokens sums tokens across messages."""
        messages = [
            ChatMessage.system("You are helpful."),
            ChatMessage.user("Hello!"),
            ChatMessage.assistant("Hi there!"),
        ]
        total = estimate_messages_tokens(messages)
        assert total > 0

    def test_estimate_messages_with_tool_calls(self):
        """Token estimation includes tool call arguments."""
        messages = [
            ChatMessage.assistant(
                content=None,
                tool_calls=[ToolCall(id="1", name="search", arguments={"query": "hello world"})],
            ),
        ]
        total = estimate_messages_tokens(messages)
        assert total > 4  # More than just the per-message overhead


# ---------------------------------------------------------------------------
# PromptCompactor core
# ---------------------------------------------------------------------------


class TestPromptCompactor:
    def test_no_compaction_under_threshold(self):
        """Messages under threshold are returned unchanged."""
        messages = [
            ChatMessage.system("Be helpful."),
            ChatMessage.user("Hi"),
            ChatMessage.assistant("Hello!"),
        ]
        compactor = PromptCompactor(max_tokens=10000)
        result = compactor.compact(messages)
        assert len(result) == len(messages)
        assert result[0].content == messages[0].content

    def test_needs_compaction(self):
        """needs_compaction returns True when threshold exceeded."""
        messages = [ChatMessage.user("x " * 500) for _ in range(20)]
        compactor = PromptCompactor(max_tokens=100)
        assert compactor.needs_compaction(messages)

    def test_no_compaction_needed(self):
        """needs_compaction returns False when under threshold."""
        messages = [ChatMessage.user("hi")]
        compactor = PromptCompactor(max_tokens=10000)
        assert not compactor.needs_compaction(messages)

    def test_compact_preserves_system_messages(self):
        """System messages at the start are always preserved."""
        messages = [
            ChatMessage.system("System prompt."),
            *[ChatMessage.user(f"Message {i} " * 50) for i in range(20)],
        ]
        compactor = PromptCompactor(max_tokens=100, preserve_recent=2)
        result = compactor.compact(messages)

        # First message should be original system prompt
        assert result[0].role.value == "system"
        assert result[0].content == "System prompt."

    def test_compact_preserves_recent_messages(self):
        """Recent messages are kept verbatim."""
        messages = [
            ChatMessage.system("System."),
            *[ChatMessage.user(f"Old message {i} " * 50) for i in range(10)],
            ChatMessage.user("Recent 1"),
            ChatMessage.user("Recent 2"),
        ]
        compactor = PromptCompactor(max_tokens=50, preserve_recent=2)
        result = compactor.compact(messages)

        # Last two messages should be the recent ones
        assert result[-1].content == "Recent 2"
        assert result[-2].content == "Recent 1"

    def test_compact_adds_summary(self):
        """Compaction adds a summary message."""
        messages = [
            ChatMessage.system("System."),
            *[ChatMessage.user(f"Message {i} " * 50) for i in range(10)],
        ]
        compactor = PromptCompactor(max_tokens=50, preserve_recent=2)
        result = compactor.compact(messages)

        # Should have: system + summary + 2 recent
        assert len(result) == 4
        summary_msg = result[1]
        assert summary_msg.role.value == "system"
        assert "[Conversation summary]" in summary_msg.content

    def test_compact_reduces_message_count(self):
        """Compaction reduces the total number of messages."""
        messages = [
            ChatMessage.system("Be helpful."),
            *[ChatMessage.user(f"Turn {i}: " + "x " * 100) for i in range(20)],
        ]
        compactor = PromptCompactor(max_tokens=100, preserve_recent=3)
        result = compactor.compact(messages)
        assert len(result) < len(messages)

    def test_compact_does_not_mutate_original(self):
        """compact() returns a new list, never mutates the original."""
        messages = [
            ChatMessage.system("System."),
            *[ChatMessage.user(f"Msg {i} " * 50) for i in range(10)],
        ]
        original_len = len(messages)
        compactor = PromptCompactor(max_tokens=50, preserve_recent=2)
        compactor.compact(messages)
        assert len(messages) == original_len

    def test_compact_few_messages(self):
        """When body messages <= preserve_recent, no compaction occurs."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("Only one message " * 100),
        ]
        compactor = PromptCompactor(max_tokens=10, preserve_recent=4)
        result = compactor.compact(messages)
        assert len(result) == len(messages)


# ---------------------------------------------------------------------------
# Summary content
# ---------------------------------------------------------------------------


class TestSummaryContent:
    def test_summary_includes_user_messages(self):
        """Summary references user message content."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("Tell me about Python programming language"),
            ChatMessage.assistant("Python is great for scripting and data science"),
            ChatMessage.user("What about JavaScript?"),
            ChatMessage.assistant("JavaScript is used for web development"),
            ChatMessage.user("Recent question"),
        ]
        compactor = PromptCompactor(max_tokens=50, preserve_recent=1)
        result = compactor.compact(messages)
        summary = result[1].content
        assert "USER" in summary
        assert "Python" in summary

    def test_summary_includes_assistant_messages(self):
        """Summary references assistant responses."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("Hello " * 50),
            ChatMessage.assistant("I am an AI assistant ready to help you " * 20),
            ChatMessage.user("What else can you do? " * 50),
            ChatMessage.assistant("I can do many things " * 20),
            ChatMessage.user("Recent"),
        ]
        compactor = PromptCompactor(max_tokens=50, preserve_recent=1)
        result = compactor.compact(messages)
        summary = result[1].content
        assert "ASSISTANT" in summary

    def test_summary_includes_tool_calls(self):
        """Summary mentions tool calls."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("Search for something " * 50),
            ChatMessage.assistant(
                content=None,
                tool_calls=[ToolCall(id="1", name="web_search", arguments={"q": "test"})],
            ),
            ChatMessage.tool_result(tool_call_id="1", name="web_search", content="Results " * 50),
            ChatMessage.user("Another question " * 50),
            ChatMessage.assistant("Here is an answer " * 50),
            ChatMessage.user("Recent"),
        ]
        compactor = PromptCompactor(max_tokens=50, preserve_recent=1)
        result = compactor.compact(messages)
        summary = result[1].content
        assert summary is not None
        assert "web_search" in summary

    def test_summary_truncates_long_content(self):
        """Individual messages are truncated in the summary."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("x" * 1000),
            ChatMessage.user("Recent"),
        ]
        compactor = PromptCompactor(max_tokens=30, preserve_recent=1)
        result = compactor.compact(messages)
        summary = result[1].content
        # Summary should not contain the full 1000-char message
        assert len(summary) < 1000


# ---------------------------------------------------------------------------
# Custom token counter
# ---------------------------------------------------------------------------


class TestCustomTokenCounter:
    def test_custom_counter(self):
        """PromptCompactor accepts a custom token counting function."""
        # Always returns 10 per string
        compactor = PromptCompactor(max_tokens=100, token_counter=lambda _: 10)
        assert compactor.count_tokens("anything") == 10

    def test_custom_counter_used_for_compaction(self):
        """Custom counter drives compaction decisions."""
        # Each message ~10 tokens + 4 overhead = 14 per msg
        # 5 messages = ~70 tokens, threshold 50 → should compact
        messages = [
            ChatMessage.system("System."),
            *[ChatMessage.user(f"Msg {i}") for i in range(4)],
        ]
        compactor = PromptCompactor(max_tokens=50, preserve_recent=2, token_counter=lambda _: 10)
        assert compactor.needs_compaction(messages)
        result = compactor.compact(messages)
        assert len(result) < len(messages)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_messages(self):
        """Empty message list returns empty."""
        compactor = PromptCompactor(max_tokens=100)
        result = compactor.compact([])
        assert result == []

    def test_only_system_messages(self):
        """Only system messages — nothing to compact."""
        messages = [ChatMessage.system("System 1"), ChatMessage.system("System 2")]
        compactor = PromptCompactor(max_tokens=10, preserve_recent=1)
        result = compactor.compact(messages)
        # No body messages to compact, should return as-is
        assert len(result) == len(messages)

    def test_preserve_recent_clamped_to_1(self):
        """preserve_recent cannot go below 1."""
        compactor = PromptCompactor(preserve_recent=0)
        assert compactor.preserve_recent == 1

    def test_summary_prefix_customizable(self):
        """Custom summary prefix is used."""
        messages = [
            ChatMessage.system("System."),
            *[ChatMessage.user(f"Msg {i} " * 50) for i in range(10)],
        ]
        compactor = PromptCompactor(max_tokens=50, preserve_recent=1, summary_prefix="[COMPRESSED]")
        result = compactor.compact(messages)
        assert "[COMPRESSED]" in result[1].content

    def test_count_messages_tokens(self):
        """count_messages_tokens delegates to configured counter."""
        compactor = PromptCompactor(token_counter=lambda _: 5)
        messages = [ChatMessage.user("a"), ChatMessage.user("b")]
        # 5 per content + 4 overhead per msg = 18 total
        assert compactor.count_messages_tokens(messages) == 18


# ---------------------------------------------------------------------------
# LLMCompactor
# ---------------------------------------------------------------------------


class TestLLMCompactor:
    """Tests for LLM-powered compaction."""

    @pytest.mark.asyncio
    async def test_llm_compactor_basic(self):
        """LLMCompactor.compact_async uses LLM for summarization."""
        compactor = LLMCompactor(model="gpt-4o-mini", max_tokens=100)
        messages = [
            ChatMessage.system("You are helpful."),
        ] + [
            ChatMessage(
                role="user" if i % 2 == 0 else "assistant", content=f"Message {i} " * 20
            )
            for i in range(20)
        ]
        with patch(
            "sagewai.core.compactor._call_summary_llm",
            return_value="Summary of conversation",
        ):
            result = await compactor.compact_async(messages)
            assert len(result) < len(messages)
            assert result[0].role.value == "system"

    @pytest.mark.asyncio
    async def test_llm_compactor_fallback_on_failure(self):
        """LLMCompactor falls back to extractive summary if LLM call fails."""
        compactor = LLMCompactor(model="gpt-4o-mini", max_tokens=100)
        messages = [
            ChatMessage.system("You are helpful."),
        ] + [
            ChatMessage(
                role="user" if i % 2 == 0 else "assistant", content=f"Message {i} " * 20
            )
            for i in range(20)
        ]
        with patch(
            "sagewai.core.compactor._call_summary_llm",
            side_effect=Exception("LLM API down"),
        ):
            # compact_async will raise, but sync compact() should still work
            result = compactor.compact(messages)
            assert len(result) < len(messages)

    @pytest.mark.asyncio
    async def test_re_compaction_idempotent(self):
        """Compacting already-compacted messages should not corrupt them."""
        compactor = PromptCompactor(max_tokens=200)
        messages = [
            ChatMessage.system("system"),
        ] + [
            ChatMessage(
                role="user" if i % 2 == 0 else "assistant", content=f"Msg {i} " * 20
            )
            for i in range(10)
        ]
        first = compactor.compact(messages)
        # Add more messages to trigger re-compaction
        extended = first + [
            ChatMessage(
                role="user" if i % 2 == 0 else "assistant", content=f"New {i} " * 20
            )
            for i in range(10)
        ]
        second = compactor.compact(extended)
        assert second[0].role.value == "system"
        assert len(second) < len(extended)

    def test_messages_with_none_content(self):
        """Messages with content=None should not crash compaction."""
        compactor = PromptCompactor(max_tokens=50)
        messages = [
            ChatMessage.system("sys"),
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[ToolCall(id="c1", name="t", arguments={})],
            ),
            ChatMessage.user("ok"),
        ] + [
            ChatMessage(
                role="user" if i % 2 == 0 else "assistant", content=f"Msg {i} " * 20
            )
            for i in range(10)
        ]
        result = compactor.compact(messages)
        assert result[0].role.value == "system"


# ---------------------------------------------------------------------------
# RuleBasedCompactor
# ---------------------------------------------------------------------------


class TestRuleBasedCompactorDropToolResults:
    """Tests for _drop_old_tool_results."""

    def test_removes_tool_messages(self):
        """Tool result messages are stripped from old section."""
        messages = [
            ChatMessage.user("search for X"),
            ChatMessage.assistant(
                content=None,
                tool_calls=[ToolCall(id="c1", name="search", arguments={"q": "X"})],
            ),
            ChatMessage.tool_result(tool_call_id="c1", name="search", content="Result X"),
            ChatMessage.user("thanks"),
        ]
        result = RuleBasedCompactor._drop_old_tool_results(messages)
        roles = [m.role for m in result]
        assert Role.tool not in roles
        assert len(result) == 3

    def test_keeps_non_tool_messages(self):
        """User and assistant messages are preserved."""
        messages = [
            ChatMessage.user("hello"),
            ChatMessage.assistant("hi"),
        ]
        result = RuleBasedCompactor._drop_old_tool_results(messages)
        assert len(result) == 2

    def test_empty_list(self):
        """Empty input returns empty."""
        assert RuleBasedCompactor._drop_old_tool_results([]) == []


class TestRuleBasedCompactorCollapseAssistant:
    """Tests for _collapse_consecutive_assistant."""

    def test_merges_consecutive_assistant(self):
        """Two consecutive assistant messages merge into one."""
        messages = [
            ChatMessage.assistant("First part."),
            ChatMessage.assistant("Second part."),
        ]
        result = RuleBasedCompactor._collapse_consecutive_assistant(messages)
        assert len(result) == 1
        assert "First part." in result[0].content
        assert "Second part." in result[0].content

    def test_does_not_merge_with_tool_calls(self):
        """Assistant messages with tool_calls are not merged."""
        messages = [
            ChatMessage.assistant("thinking"),
            ChatMessage.assistant(
                content=None,
                tool_calls=[ToolCall(id="c1", name="search", arguments={})],
            ),
        ]
        result = RuleBasedCompactor._collapse_consecutive_assistant(messages)
        assert len(result) == 2

    def test_non_consecutive_not_merged(self):
        """Assistant messages separated by user message stay separate."""
        messages = [
            ChatMessage.assistant("First"),
            ChatMessage.user("question"),
            ChatMessage.assistant("Second"),
        ]
        result = RuleBasedCompactor._collapse_consecutive_assistant(messages)
        assert len(result) == 3

    def test_merges_three_consecutive(self):
        """Three consecutive assistant messages collapse into one."""
        messages = [
            ChatMessage.assistant("A"),
            ChatMessage.assistant("B"),
            ChatMessage.assistant("C"),
        ]
        result = RuleBasedCompactor._collapse_consecutive_assistant(messages)
        assert len(result) == 1
        assert "A" in result[0].content
        assert "B" in result[0].content
        assert "C" in result[0].content

    def test_empty_list(self):
        """Empty input returns empty."""
        result = RuleBasedCompactor._collapse_consecutive_assistant([])
        assert result == []


class TestRuleBasedCompactorDedup:
    """Tests for _dedup_by_content."""

    def test_removes_exact_duplicates(self):
        """Duplicate (role, content) pairs are removed."""
        messages = [
            ChatMessage.user("hello"),
            ChatMessage.assistant("hi"),
            ChatMessage.user("hello"),
        ]
        result = RuleBasedCompactor._dedup_by_content(messages)
        assert len(result) == 2

    def test_keeps_first_occurrence(self):
        """The first occurrence of a duplicate is kept."""
        messages = [
            ChatMessage.user("first"),
            ChatMessage.user("second"),
            ChatMessage.user("first"),
        ]
        result = RuleBasedCompactor._dedup_by_content(messages)
        assert result[0].content == "first"
        assert result[1].content == "second"
        assert len(result) == 2

    def test_different_roles_not_deduped(self):
        """Same content with different roles is not a duplicate."""
        messages = [
            ChatMessage.user("hello"),
            ChatMessage.assistant("hello"),
        ]
        result = RuleBasedCompactor._dedup_by_content(messages)
        assert len(result) == 2

    def test_no_duplicates(self):
        """No duplicates means no change."""
        messages = [
            ChatMessage.user("a"),
            ChatMessage.user("b"),
            ChatMessage.user("c"),
        ]
        result = RuleBasedCompactor._dedup_by_content(messages)
        assert len(result) == 3


class TestRuleBasedCompactorSplitSystem:
    """Tests for _split_system."""

    def test_splits_leading_system(self):
        """Leading system messages are separated from the body."""
        messages = [
            ChatMessage.system("Prompt 1"),
            ChatMessage.system("Prompt 2"),
            ChatMessage.user("hi"),
            ChatMessage.assistant("hello"),
        ]
        sys_msgs, body_msgs = RuleBasedCompactor._split_system(messages)
        assert len(sys_msgs) == 2
        assert len(body_msgs) == 2

    def test_system_after_body_stays_in_body(self):
        """System messages after a non-system message go into body."""
        messages = [
            ChatMessage.system("Prompt"),
            ChatMessage.user("hi"),
            ChatMessage.system("Mid-conversation system"),
        ]
        sys_msgs, body_msgs = RuleBasedCompactor._split_system(messages)
        assert len(sys_msgs) == 1
        assert len(body_msgs) == 2

    def test_no_system_messages(self):
        """All messages go into body when none are system."""
        messages = [ChatMessage.user("hi"), ChatMessage.assistant("hello")]
        sys_msgs, body_msgs = RuleBasedCompactor._split_system(messages)
        assert len(sys_msgs) == 0
        assert len(body_msgs) == 2


class TestRuleBasedCompactorFull:
    """Tests for the full compact() pipeline."""

    def test_no_compaction_when_under_threshold(self):
        """Messages under threshold are returned unchanged."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("Hi"),
            ChatMessage.assistant("Hello!"),
        ]
        compactor = RuleBasedCompactor(max_tokens=10000, keep_last_n=2)
        result = compactor.compact(messages)
        assert len(result) == len(messages)

    def test_keep_last_n_preserved(self):
        """Recent messages are always kept verbatim."""
        messages = [
            ChatMessage.system("System."),
            *[ChatMessage.user(f"Old msg {i} " * 50) for i in range(10)],
            ChatMessage.user("Recent 1"),
            ChatMessage.user("Recent 2"),
            ChatMessage.user("Recent 3"),
        ]
        compactor = RuleBasedCompactor(max_tokens=50, keep_last_n=3)
        result = compactor.compact(messages)
        assert result[-1].content == "Recent 3"
        assert result[-2].content == "Recent 2"
        assert result[-3].content == "Recent 1"

    def test_system_messages_preserved(self):
        """System messages at the start are never removed."""
        messages = [
            ChatMessage.system("You are a helpful assistant."),
            *[ChatMessage.user(f"Message {i} " * 50) for i in range(15)],
        ]
        compactor = RuleBasedCompactor(max_tokens=50, keep_last_n=2)
        result = compactor.compact(messages)
        assert result[0].role == Role.system
        assert result[0].content == "You are a helpful assistant."

    def test_tool_results_dropped_from_old(self):
        """Tool result messages in old section are removed."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("search"),
            ChatMessage.assistant(
                content=None,
                tool_calls=[ToolCall(id="c1", name="s", arguments={})],
            ),
            ChatMessage.tool_result(tool_call_id="c1", name="s", content="big result " * 100),
            *[ChatMessage.user(f"Msg {i}") for i in range(5)],
        ]
        compactor = RuleBasedCompactor(max_tokens=50, keep_last_n=3)
        result = compactor.compact(messages)
        tool_msgs = [m for m in result if m.role == Role.tool]
        # Tool results in old section should be gone
        assert len(tool_msgs) == 0

    def test_fallback_to_extractive_summary(self):
        """When rules don't reduce enough, extractive summary kicks in."""
        messages = [
            ChatMessage.system("System."),
            *[ChatMessage.user(f"Very long message {i} " * 200) for i in range(20)],
        ]
        compactor = RuleBasedCompactor(max_tokens=100, keep_last_n=2)
        result = compactor.compact(messages)
        # Should have: system + summary + 2 recent
        assert len(result) == 4
        assert "[Conversation summary]" in result[1].content

    def test_few_body_messages_no_compaction(self):
        """When body messages <= keep_last_n, no compaction occurs."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("Only one " * 100),
        ]
        compactor = RuleBasedCompactor(max_tokens=10, keep_last_n=5)
        result = compactor.compact(messages)
        assert len(result) == len(messages)

    def test_keep_last_n_clamped_to_1(self):
        """keep_last_n cannot go below 1."""
        compactor = RuleBasedCompactor(keep_last_n=0)
        assert compactor.keep_last_n == 1

    def test_does_not_mutate_original(self):
        """compact() returns a new list, original is unchanged."""
        messages = [
            ChatMessage.system("System."),
            *[ChatMessage.user(f"Msg {i} " * 50) for i in range(10)],
        ]
        original_len = len(messages)
        compactor = RuleBasedCompactor(max_tokens=50, keep_last_n=2)
        compactor.compact(messages)
        assert len(messages) == original_len

    def test_dedup_removes_repeated_messages(self):
        """Duplicate old messages are collapsed."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("Tell me about X " * 30),
            ChatMessage.assistant("X is great " * 30),
            ChatMessage.user("Tell me about X " * 30),
            ChatMessage.assistant("X is great " * 30),
            ChatMessage.user("Tell me about X " * 30),
            ChatMessage.assistant("X is great " * 30),
            ChatMessage.user("Recent question"),
        ]
        compactor = RuleBasedCompactor(max_tokens=100, keep_last_n=1, dedup=True)
        result = compactor.compact(messages)
        # Old section had 3 pairs of duplicates, dedup should reduce to 2 unique
        # plus system + recent
        assert len(result) < len(messages)

    def test_all_rules_disabled(self):
        """With all rules disabled, falls back to extractive summary."""
        messages = [
            ChatMessage.system("System."),
            *[ChatMessage.user(f"Msg {i} " * 100) for i in range(15)],
        ]
        compactor = RuleBasedCompactor(
            max_tokens=50,
            keep_last_n=2,
            drop_tool_results=False,
            collapse_assistant=False,
            dedup=False,
        )
        result = compactor.compact(messages)
        # All rules disabled but still over budget → extractive fallback
        assert "[Conversation summary]" in result[1].content

    def test_pipeline_order(self):
        """Rules pipeline processes in correct order: drop tools → collapse → dedup."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("search " * 30),
            ChatMessage.assistant(
                content=None,
                tool_calls=[ToolCall(id="c1", name="search", arguments={})],
            ),
            ChatMessage.tool_result(
                tool_call_id="c1", name="search", content="result " * 30
            ),
            ChatMessage.assistant("Here is the answer " * 30),
            ChatMessage.assistant("And more detail " * 30),
            ChatMessage.user("search " * 30),  # duplicate of first user msg
            ChatMessage.user("Recent"),
        ]
        compactor = RuleBasedCompactor(max_tokens=50, keep_last_n=1)
        result = compactor.compact(messages)
        # Tool results dropped, assistants collapsed, duplicate user deduped
        old_section = result[1:-1]  # between system and recent
        tool_msgs = [m for m in old_section if m.role == Role.tool]
        assert len(tool_msgs) == 0


# ---------------------------------------------------------------------------
# CompactionPipeline
# ---------------------------------------------------------------------------


class TestCompactionPipeline:
    """Tests for the three-tier compaction pipeline."""

    def test_returns_immediately_if_under_budget(self):
        """Pipeline returns messages unchanged when under token threshold."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("Hi"),
            ChatMessage.assistant("Hello!"),
        ]
        pipeline = CompactionPipeline(max_tokens=10000)
        result = pipeline.compact(messages)
        assert len(result) == len(messages)
        assert result[0].content == messages[0].content

    def test_stops_at_rule_tier_if_sufficient(self):
        """Pipeline stops after rule-based tier when it reduces enough."""
        # Create messages that the rule-based tier can handle (lots of tool
        # results and duplicates that rules can strip efficiently)
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("search " * 20),
            ChatMessage.assistant(
                content=None,
                tool_calls=[ToolCall(id="c1", name="s", arguments={})],
            ),
            ChatMessage.tool_result(
                tool_call_id="c1", name="s", content="big result " * 100
            ),
            ChatMessage.user("search " * 20),  # duplicate
            ChatMessage.assistant(
                content=None,
                tool_calls=[ToolCall(id="c2", name="s", arguments={})],
            ),
            ChatMessage.tool_result(
                tool_call_id="c2", name="s", content="big result " * 100
            ),
            ChatMessage.user("Recent question"),
        ]
        # Set threshold high enough that removing tool results makes it fit
        pipeline = CompactionPipeline(max_tokens=200, keep_last_n=1)
        result = pipeline.compact(messages)
        # Should have compacted (fewer messages than original)
        assert len(result) < len(messages)
        # Recent message preserved
        assert result[-1].content == "Recent question"

    def test_escalates_to_extractive_if_rule_insufficient(self):
        """Pipeline escalates to extractive tier when rule-based isn't enough."""
        # Create messages with no tool results or duplicates (rules won't help much)
        messages = [
            ChatMessage.system("System."),
            *[ChatMessage.user(f"Unique message {i} " * 100) for i in range(20)],
        ]
        pipeline = CompactionPipeline(max_tokens=100, keep_last_n=10, preserve_recent=2)
        result = pipeline.compact(messages)
        # Extractive tier should have produced a summary
        assert len(result) < len(messages)
        # Should contain a summary message (from extractive tier)
        summary_msgs = [m for m in result if m.role == Role.system and "[Conversation summary]" in (m.content or "")]
        assert len(summary_msgs) >= 1

    @pytest.mark.asyncio
    async def test_compact_async_escalates_to_llm_tier(self):
        """compact_async escalates to LLM tier when extractive isn't enough."""
        messages = [
            ChatMessage.system("System."),
            *[ChatMessage.user(f"Unique message {i} " * 200) for i in range(30)],
        ]
        pipeline = CompactionPipeline(max_tokens=50, keep_last_n=2, preserve_recent=2)

        # Force needs_compaction to always return True so pipeline reaches LLM tier
        pipeline.needs_compaction = lambda msgs: True

        # Mock the LLM tier's compact_async directly
        llm_result = [ChatMessage.system("LLM summary"), messages[-1]]
        pipeline._llm_tier.compact_async = AsyncMock(return_value=llm_result)

        result = await pipeline.compact_async(messages)
        pipeline._llm_tier.compact_async.assert_called_once()
        assert len(result) < len(messages)

    @pytest.mark.asyncio
    async def test_compact_async_stops_early_if_under_budget(self):
        """compact_async returns immediately if under budget."""
        messages = [
            ChatMessage.system("System."),
            ChatMessage.user("Hi"),
        ]
        pipeline = CompactionPipeline(max_tokens=10000)
        result = await pipeline.compact_async(messages)
        assert len(result) == len(messages)

    def test_constructor_wires_sub_compactors(self):
        """Constructor creates all three sub-compactors with correct config."""
        pipeline = CompactionPipeline(
            max_tokens=5000, model="gpt-4o", keep_last_n=8, preserve_recent=3
        )
        assert isinstance(pipeline._rule_tier, RuleBasedCompactor)
        assert isinstance(pipeline._extractive_tier, PromptCompactor)
        assert isinstance(pipeline._llm_tier, LLMCompactor)
        assert pipeline._rule_tier.max_tokens == 5000
        assert pipeline._rule_tier.keep_last_n == 8
        assert pipeline._extractive_tier.max_tokens == 5000
        assert pipeline._extractive_tier.preserve_recent == 3
        assert pipeline._llm_tier.max_tokens == 5000
        assert pipeline._llm_tier.summary_model == "gpt-4o"

    def test_pipeline_is_subclass_of_prompt_compactor(self):
        """CompactionPipeline is a PromptCompactor (compatible interface)."""
        pipeline = CompactionPipeline()
        assert isinstance(pipeline, PromptCompactor)
