# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Hybrid fact extractor — rules first, LLM fills gaps."""

from __future__ import annotations

from sagewai.intelligence.extractors.llm_fact_extractor import LLMFactExtractor
from sagewai.intelligence.extractors.rule_based import RuleBasedFactExtractor
from sagewai.intelligence.models import ExtractedFact


class HybridFactExtractor:
    """Combines rule-based and LLM extraction.

    Runs the rule-based extractor first (fast, free). If it finds fewer than
    ``llm_threshold`` facts and an LLM extractor is available, the LLM is
    called to fill gaps.

    Parameters
    ----------
    rule_extractor:
        Rule-based backend (defaults to a fresh ``RuleBasedFactExtractor``).
    llm_extractor:
        Optional LLM backend. When ``None`` only rules are used.
    llm_threshold:
        Minimum fact count from rules before the LLM is skipped.
    """

    def __init__(
        self,
        rule_extractor: RuleBasedFactExtractor | None = None,
        llm_extractor: LLMFactExtractor | None = None,
        llm_threshold: int = 2,
    ) -> None:
        self._rules = rule_extractor or RuleBasedFactExtractor()
        self._llm = llm_extractor
        self._llm_threshold = llm_threshold

    async def extract(self, conversation: str) -> list[ExtractedFact]:
        """Extract facts: rules first, then optionally LLM."""
        facts = await self._rules.extract(conversation)

        if self._llm and len(facts) < self._llm_threshold:
            llm_facts = await self._llm.extract(conversation)
            facts.extend(llm_facts)

        return self._deduplicate(facts)

    @staticmethod
    def _deduplicate(facts: list[ExtractedFact]) -> list[ExtractedFact]:
        """Remove near-duplicate facts."""
        seen: set[str] = set()
        unique: list[ExtractedFact] = []
        for fact in facts:
            key = fact.content.lower().strip()[:50]
            if key not in seen:
                seen.add(key)
                unique.append(fact)
        return unique
