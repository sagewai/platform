# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LLM-powered fact extractor — wraps existing litellm extraction pattern."""

from __future__ import annotations

import json
import logging

from sagewai.intelligence.models import ExtractedFact

logger = logging.getLogger(__name__)


class LLMFactExtractor:
    """Fact extraction via LLM prompt (existing pattern from MemoryBridge).

    Parameters
    ----------
    model:
        LLM model for extraction (cheap/fast recommended, e.g. ``gpt-4o-mini``).
    """

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self._model = model

    async def extract(self, conversation: str) -> list[ExtractedFact]:
        """Extract facts from conversation text using an LLM.

        Falls back to empty list on any LLM error.
        """
        try:
            import litellm
        except ImportError:
            logger.warning("litellm not installed, cannot run LLM extraction")
            return []

        prompt = (
            "Extract key facts, decisions, and user preferences from this "
            "conversation. Return a JSON array of objects, each with keys: "
            '"content" (string), "fact_type" (one of "decision", "preference", '
            '"entity", "event", "action", "general"), and "confidence" (float '
            "0-1). Only include information worth remembering. "
            "If nothing noteworthy, return [].\n\n"
            f"{conversation}\n\n"
            "Facts (JSON array):"
        )

        try:
            response = await litellm.acompletion(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=500,
            )
            text = response.choices[0].message.content or "[]"
            return self._parse_response(text)
        except Exception:
            logger.warning("LLM fact extraction failed", exc_info=True)
            return []

    @staticmethod
    def _parse_response(text: str) -> list[ExtractedFact]:
        """Parse LLM response into ExtractedFact objects."""
        try:
            data = json.loads(text)
            if not isinstance(data, list):
                return []
        except json.JSONDecodeError:
            # Fallback: treat as newline-delimited plain facts
            lines = [
                line.strip().lstrip("- \u2022").strip()
                for line in text.split("\n")
            ]
            data = [{"content": line} for line in lines if line]

        facts: list[ExtractedFact] = []
        for item in data:
            if isinstance(item, str):
                facts.append(ExtractedFact(content=item))
            elif isinstance(item, dict) and "content" in item:
                facts.append(
                    ExtractedFact(
                        content=str(item["content"]),
                        fact_type=str(item.get("fact_type", "general")),
                        confidence=float(item.get("confidence", 0.9)),
                        entities=list(item.get("entities", [])),
                    )
                )
        return facts
