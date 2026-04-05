# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Prompt Compactor — token-aware context compression for long conversations.

When conversation history exceeds a configurable token threshold, the compactor
semantically compresses older messages into a summary while retaining the system
prompt and recent messages. This prevents agents from losing mission context
during long-running conversations.

Usage::

    from sagewai.core.compactor import PromptCompactor

    compactor = PromptCompactor(max_tokens=4000, preserve_recent=4)
    messages = compactor.compact(messages)

Integration with BaseAgent::

    agent = UniversalAgent(name="Assistant", model="gpt-4o")
    agent.compactor = PromptCompactor(max_tokens=4000)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sagewai.models.message import ChatMessage, Role

logger = logging.getLogger(__name__)

# Default token-to-character ratio (approximate, conservative).
# Real tokenizers average ~4 chars/token for English text.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count from text using character-based heuristic.

    For production accuracy, swap this for ``litellm.token_counter`` or
    ``tiktoken``. The heuristic is intentionally conservative (over-estimates)
    to trigger compaction slightly early rather than too late.
    """
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: list[ChatMessage]) -> int:
    """Estimate total token count for a list of messages."""
    total = 0
    for msg in messages:
        if msg.content:
            total += estimate_tokens(msg.content)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                total += estimate_tokens(str(tc.arguments))
        # Per-message overhead (~4 tokens for role, delimiters)
        total += 4
    return total


class PromptCompactor:
    """Token-aware conversation compressor.

    Parameters
    ----------
    max_tokens:
        Trigger compaction when estimated token count exceeds this.
    preserve_recent:
        Number of recent messages to keep verbatim (never compressed).
    summary_max_tokens:
        Target token budget for the generated summary message.
    summary_prefix:
        Prefix for the summary system message.
    token_counter:
        Optional callable ``(str) -> int`` for accurate token counting.
        Defaults to character-based estimation.
    """

    def __init__(
        self,
        *,
        max_tokens: int = 4000,
        preserve_recent: int = 4,
        summary_max_tokens: int = 500,
        summary_prefix: str = "[Conversation summary]",
        token_counter: Any = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.preserve_recent = max(1, preserve_recent)
        self.summary_max_tokens = summary_max_tokens
        self.summary_prefix = summary_prefix
        self._token_counter = token_counter or estimate_tokens

    def count_tokens(self, text: str) -> int:
        """Count tokens in text using the configured counter."""
        return self._token_counter(text)

    def count_messages_tokens(self, messages: list[ChatMessage]) -> int:
        """Estimate total tokens for a message list."""
        total = 0
        for msg in messages:
            if msg.content:
                total += self.count_tokens(msg.content)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total += self.count_tokens(str(tc.arguments))
            total += 4
        return total

    def needs_compaction(self, messages: list[ChatMessage]) -> bool:
        """Check whether the conversation exceeds the token threshold."""
        return self.count_messages_tokens(messages) > self.max_tokens

    def compact(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Compress conversation history if it exceeds the token threshold.

        Returns a new list. The original is not mutated.

        Strategy:
        1. Keep all system messages at the start.
        2. Keep the ``preserve_recent`` most recent non-system messages.
        3. Summarize everything in between into a single system message.
        """
        if not self.needs_compaction(messages):
            return list(messages)

        # Separate system prefix from conversation body
        system_msgs: list[ChatMessage] = []
        body_msgs: list[ChatMessage] = []
        for msg in messages:
            if msg.role == Role.system and not body_msgs:
                system_msgs.append(msg)
            else:
                body_msgs.append(msg)

        # Nothing to compact
        if len(body_msgs) <= self.preserve_recent:
            return list(messages)

        # Split: old messages to summarize, recent to preserve
        split_idx = len(body_msgs) - self.preserve_recent
        old_msgs = body_msgs[:split_idx]
        recent_msgs = body_msgs[split_idx:]

        summary = self._summarize(old_msgs)
        summary_msg = ChatMessage.system(f"{self.summary_prefix}\n{summary}")

        result = system_msgs + [summary_msg] + recent_msgs

        logger.info(
            "Compacted %d messages → %d (removed %d, added summary)",
            len(messages),
            len(result),
            len(old_msgs),
        )
        return result

    def _summarize(self, messages: list[ChatMessage]) -> str:
        """Create an extractive summary of messages.

        This is a local, synchronous summary (no LLM call). It extracts key
        content from each message, preserving speaker turns and tool results.
        For LLM-powered abstractive summaries, subclass and override this method.
        """
        parts: list[str] = []
        for msg in messages:
            prefix = msg.role.value.upper()
            if msg.content:
                # Truncate long messages to keep summary concise
                text = self._truncate(msg.content, max_chars=200)
                parts.append(f"- {prefix}: {text}")
            if msg.tool_calls:
                tool_names = ", ".join(tc.name for tc in msg.tool_calls)
                parts.append(f"- {prefix} called tools: {tool_names}")
            if msg.role == Role.tool and msg.name:
                text = self._truncate(msg.content or "", max_chars=100)
                parts.append(f"- TOOL ({msg.name}): {text}")

        summary = "\n".join(parts)

        # Enforce summary token budget
        target_chars = self.summary_max_tokens * _CHARS_PER_TOKEN
        if len(summary) > target_chars:
            summary = summary[:target_chars].rsplit("\n", 1)[0] + "\n- [... truncated]"

        return summary

    @staticmethod
    def _truncate(text: str, max_chars: int = 200) -> str:
        """Truncate text to max_chars, adding ellipsis if needed."""
        # Collapse whitespace first
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rsplit(" ", 1)[0] + "..."


async def _call_summary_llm(model: str, prompt: str) -> str:
    """Call LLM for summarization via LiteLLM."""
    import litellm

    response = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=500,
    )
    return response.choices[0].message.content or ""


class RuleBasedCompactor(PromptCompactor):
    """Deterministic rule-based compactor (no LLM call).

    Inspired by Claude Code's COMPACT command. Applies a pipeline of
    rules to reduce context size without any model calls.

    Parameters
    ----------
    keep_last_n:
        Number of recent non-system messages to preserve verbatim.
    drop_tool_results:
        When True, drop all tool result messages from old (compactable)
        section. Tool results are typically the most expendable content.
    collapse_assistant:
        Merge consecutive assistant messages into the last one.
    dedup:
        Remove duplicate messages by content hash.
    """

    def __init__(
        self,
        *,
        keep_last_n: int = 10,
        drop_tool_results: bool = True,
        collapse_assistant: bool = True,
        dedup: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.keep_last_n = max(1, keep_last_n)
        self.drop_tool_results = drop_tool_results
        self.collapse_assistant = collapse_assistant
        self.dedup = dedup

    def compact(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Compress conversation using deterministic rules.

        Strategy:
        1. Separate system prefix from conversation body.
        2. Split body into old (compactable) and recent (preserved) sections.
        3. Apply rule pipeline to old messages: drop tool results, collapse
           consecutive assistant messages, deduplicate by content hash.
        4. If still over budget, fall back to extractive summarization of
           the remaining old messages.
        """
        if not self.needs_compaction(messages):
            return list(messages)

        # Separate system prefix
        system_msgs, body_msgs = self._split_system(messages)

        if len(body_msgs) <= self.keep_last_n:
            return list(messages)

        # Split into old (compactable) and recent (preserved)
        split_idx = len(body_msgs) - self.keep_last_n
        old_msgs = body_msgs[:split_idx]
        recent_msgs = body_msgs[split_idx:]

        # Apply rules pipeline to old messages
        processed = list(old_msgs)
        if self.drop_tool_results:
            processed = self._drop_old_tool_results(processed)
        if self.collapse_assistant:
            processed = self._collapse_consecutive_assistant(processed)
        if self.dedup:
            processed = self._dedup_by_content(processed)

        result = system_msgs + processed + recent_msgs

        # If still over budget, fall back to extractive summary of remaining old msgs
        if self.needs_compaction(result) and processed:
            summary = self._summarize(processed)
            summary_msg = ChatMessage.system(f"{self.summary_prefix}\n{summary}")
            result = system_msgs + [summary_msg] + recent_msgs

        logger.info(
            "Rule-compacted %d messages → %d",
            len(messages),
            len(result),
        )
        return result

    @staticmethod
    def _split_system(
        messages: list[ChatMessage],
    ) -> tuple[list[ChatMessage], list[ChatMessage]]:
        """Separate leading system messages from the conversation body."""
        system_msgs: list[ChatMessage] = []
        body_msgs: list[ChatMessage] = []
        for msg in messages:
            if msg.role == Role.system and not body_msgs:
                system_msgs.append(msg)
            else:
                body_msgs.append(msg)
        return system_msgs, body_msgs

    @staticmethod
    def _drop_old_tool_results(messages: list[ChatMessage]) -> list[ChatMessage]:
        """Remove tool result messages — the most expendable content."""
        return [m for m in messages if m.role != Role.tool]

    @staticmethod
    def _collapse_consecutive_assistant(
        messages: list[ChatMessage],
    ) -> list[ChatMessage]:
        """Merge consecutive assistant messages into the last one.

        Messages with tool_calls are never merged to preserve tool-call
        pairing integrity.
        """
        if not messages:
            return messages
        result: list[ChatMessage] = []
        for msg in messages:
            if (
                result
                and msg.role == Role.assistant
                and result[-1].role == Role.assistant
                and not msg.tool_calls
            ):
                merged_content = (result[-1].content or "") + "\n" + (msg.content or "")
                result[-1] = ChatMessage.assistant(content=merged_content.strip())
            else:
                result.append(msg)
        return result

    @staticmethod
    def _dedup_by_content(messages: list[ChatMessage]) -> list[ChatMessage]:
        """Remove duplicate messages by (role, content) hash, keeping first."""
        seen: set[int] = set()
        result: list[ChatMessage] = []
        for msg in messages:
            key = hash((msg.role, msg.content or ""))
            if key not in seen:
                seen.add(key)
                result.append(msg)
        return result


class LLMCompactor(PromptCompactor):
    """LLM-powered conversation compressor.

    Overrides the extractive ``_summarize()`` with an async LLM call
    for abstractive summarization. Use ``compact_async()`` and
    ``summarize_async()`` for the LLM-powered path. The sync
    ``compact()`` still falls back to extractive summarization.

    Parameters
    ----------
    model:
        LLM model for summarization (e.g., "gpt-4o-mini").
    """

    def __init__(self, *, model: str = "gpt-4o-mini", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.summary_model = model

    async def summarize_async(self, messages: list[ChatMessage]) -> str:
        """Abstractive summarization using an LLM."""
        conversation_text = "\n".join(
            f"{msg.role.value.upper()}: {msg.content or '[tool call]'}" for msg in messages
        )
        prompt = (
            "Summarize the following conversation concisely. "
            "Preserve: key decisions, user preferences, task state, "
            "important facts. Discard: greetings, verbose back-and-forth, "
            "repeated context.\n\n"
            f"{conversation_text}\n\n"
            "Summary:"
        )
        return await _call_summary_llm(self.summary_model, prompt)

    async def compact_async(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Async compact with LLM-powered summarization.

        Same strategy as ``compact()`` but uses ``summarize_async()``
        instead of the extractive ``_summarize()``.
        """
        if not self.needs_compaction(messages):
            return list(messages)

        system_msgs: list[ChatMessage] = []
        body_msgs: list[ChatMessage] = []
        for msg in messages:
            if msg.role == Role.system and not body_msgs:
                system_msgs.append(msg)
            else:
                body_msgs.append(msg)

        if len(body_msgs) <= self.preserve_recent:
            return list(messages)

        split_idx = len(body_msgs) - self.preserve_recent
        old_msgs = body_msgs[:split_idx]
        recent_msgs = body_msgs[split_idx:]

        summary = await self.summarize_async(old_msgs)
        summary_msg = ChatMessage.system(f"{self.summary_prefix}\n{summary}")

        result = system_msgs + [summary_msg] + recent_msgs

        logger.info(
            "LLM-compacted %d messages → %d (removed %d, added summary)",
            len(messages),
            len(result),
            len(old_msgs),
        )
        return result


class CompactionPipeline(PromptCompactor):
    """Three-tier compaction pipeline: rule -> extractive -> LLM.

    Each tier runs only if the previous didn't reduce tokens below
    the configured threshold. Most conversations never need the
    expensive LLM tier.

    Parameters
    ----------
    max_tokens:
        Target token budget. Tiers run progressively until under this.
    model:
        LLM model for the final tier (only used if needed).
    keep_last_n:
        Messages preserved by the rule-based tier.
    """

    def __init__(
        self,
        *,
        max_tokens: int = 4000,
        model: str = "gpt-4o-mini",
        keep_last_n: int = 10,
        preserve_recent: int = 4,
        **kwargs: Any,
    ) -> None:
        super().__init__(max_tokens=max_tokens, preserve_recent=preserve_recent, **kwargs)
        self._rule_tier = RuleBasedCompactor(
            max_tokens=max_tokens, keep_last_n=keep_last_n
        )
        self._extractive_tier = PromptCompactor(
            max_tokens=max_tokens, preserve_recent=preserve_recent
        )
        self._llm_tier = LLMCompactor(
            model=model, max_tokens=max_tokens, preserve_recent=preserve_recent
        )

    def compact(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Sync compact: runs rule-based, then extractive if still over budget."""
        if not self.needs_compaction(messages):
            return list(messages)

        # Tier 1: Rule-based (free, fast)
        result = self._rule_tier.compact(messages)
        if not self.needs_compaction(result):
            logger.info("Pipeline: rule-based tier sufficient")
            return result

        # Tier 2: Extractive (no LLM)
        result = self._extractive_tier.compact(result)
        logger.info("Pipeline: extractive tier applied")
        return result

    async def compact_async(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Async compact: runs all 3 tiers progressively."""
        if not self.needs_compaction(messages):
            return list(messages)

        # Tier 1: Rule-based
        result = self._rule_tier.compact(messages)
        if not self.needs_compaction(result):
            logger.info("Pipeline: rule-based tier sufficient")
            return result

        # Tier 2: Extractive
        result = self._extractive_tier.compact(result)
        if not self.needs_compaction(result):
            logger.info("Pipeline: extractive tier sufficient")
            return result

        # Tier 3: LLM-powered (costly)
        result = await self._llm_tier.compact_async(result)
        logger.info("Pipeline: LLM tier applied")
        return result
