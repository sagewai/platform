# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""MemoryWriter — auto-extract key facts from conversations.

Runs a lightweight LLM pass to identify decisions, preferences, and
important facts from recent conversation turns, then stores them
in a MemoryProvider for future retrieval.

Usage::

    writer = MemoryWriter(model="gpt-4o-mini")
    if writer.should_extract(turn_count=10):
        await writer.extract_and_store(messages, memory)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sagewai.intelligence.extractors.protocol import FactExtractor
from sagewai.models.message import ChatMessage

logger = logging.getLogger(__name__)


async def _call_extraction_llm(model: str, prompt: str) -> list[str]:
    """Call LLM to extract facts from conversation."""
    import litellm

    response = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=500,
    )
    text = response.choices[0].message.content or "[]"
    try:
        facts = json.loads(text)
        if isinstance(facts, list):
            return [str(f) for f in facts]
    except json.JSONDecodeError:
        # Fallback: split by newlines, strip bullets
        lines = [line.strip().lstrip("- \u2022").strip() for line in text.split("\n")]
        return [line for line in lines if line]
    return []


class MemoryWriter:
    """Auto-extract and store facts from agent conversations.

    Parameters
    ----------
    model:
        LLM model for extraction (cheap/fast recommended).
    extract_every_n_turns:
        Run extraction every N conversation turns.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        extract_every_n_turns: int = 5,
        fact_extractor: FactExtractor | None = None,
    ) -> None:
        self.model = model
        self.extract_every_n_turns = extract_every_n_turns
        self._fact_extractor = fact_extractor

    def should_extract(self, turn_count: int, compaction_happened: bool = False) -> bool:
        """Determine whether extraction should run this turn."""
        if compaction_happened:
            return True
        return turn_count > 0 and turn_count % self.extract_every_n_turns == 0

    async def extract(self, messages: list[ChatMessage]) -> list[str]:
        """Extract key facts from conversation messages.

        Returns a list of fact strings.
        """
        if not messages:
            return []

        conversation_text = "\n".join(
            f"{msg.role.value.upper()}: {msg.content or '[tool interaction]'}"
            for msg in messages
            if msg.content
        )

        # Use pluggable fact extractor when available
        if self._fact_extractor is not None:
            extracted = await self._fact_extractor.extract(conversation_text)
            return [f.content for f in extracted]

        prompt = (
            "Extract key facts, decisions, and user preferences from this "
            "conversation. Return a JSON array of strings, each being one "
            "concise fact. Only include information worth remembering for "
            "future conversations. If there is nothing noteworthy, return [].\n\n"
            f"{conversation_text}\n\n"
            "Facts (JSON array):"
        )
        return await _call_extraction_llm(self.model, prompt)

    async def extract_and_store(self, messages: list[ChatMessage], memory: Any) -> list[str]:
        """Extract facts and store them in a memory provider.

        Args:
            messages: Conversation messages to extract from.
            memory: A MemoryProvider with an async ``store(content, metadata)`` method.

        Returns:
            List of extracted fact strings.
        """
        facts = await self.extract(messages)
        for fact in facts:
            try:
                await memory.store(fact, metadata={"source": "memory_writer"})
            except Exception:
                logger.exception("Failed to store fact: %s", fact[:100])
        if facts:
            logger.info("MemoryWriter stored %d facts", len(facts))
        return facts
