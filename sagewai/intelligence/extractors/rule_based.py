# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Rule-based fact extractor — pattern matching without LLM.

Detects decisions, preferences, named entities, events, and action items
using regular expressions. Works offline with zero API keys.
"""

from __future__ import annotations

import re

from sagewai.intelligence.models import ExtractedFact

# ---------------------------------------------------------------------------
# Pattern groups
# ---------------------------------------------------------------------------

_DECISION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:we|they|I)\s+(?:decided|agreed|chose|selected|picked)"
        r"\s+(?:to\s+)?(.{10,100})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:the\s+)?(?:decision|plan|approach)\s+is\s+(?:to\s+)?(.{10,100})",
        re.IGNORECASE,
    ),
    re.compile(
        r"let'?s\s+(?:go\s+with|use|try|do)\s+(.{5,80})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:we|I)\s+(?:will|are\s+going\s+to)\s+(?:go\s+with|stick\s+with)"
        r"\s+(.{5,80})",
        re.IGNORECASE,
    ),
]

_PREFERENCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"I\s+(?:prefer|like|want|need|love|hate|don'?t\s+like)\s+(.{5,100})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:my|our)\s+(?:preference|choice|favorite)\s+is\s+(.{5,80})",
        re.IGNORECASE,
    ),
]

_EVENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:scheduled|meeting|deadline|due\s+date|appointment)"
        r"\s+(?:for|on|is|at)\s+(.{5,60})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:on|at|by)\s+(\w{3,}\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)",
        re.IGNORECASE,
    ),
]

_ACTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:I\s+will|we\s+should|we\s+need\s+to|TODO:?\s*|action\s+item:?\s*)"
        r"(.{5,100})",
        re.IGNORECASE,
    ),
]

# Entity-level patterns (emails, URLs, @mentions)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL_RE = re.compile(r"https?://[^\s,)]+")
_MENTION_RE = re.compile(r"@(\w{2,30})")


class RuleBasedFactExtractor:
    """Extract facts from conversations using pattern matching.

    Detects:
    - Decisions: "we decided", "the plan is", "let's go with", "agreed to"
    - Preferences: "I prefer", "I like", "I want", "I need", "I don't like"
    - Named entities: @mentions, URLs, emails
    - Events: "scheduled for", "meeting on", "deadline is", "due date"
    - Actions: "I will", "we should", "TODO:", "action item"
    """

    async def extract(self, conversation: str) -> list[ExtractedFact]:
        """Extract facts from conversation text via regex patterns."""
        facts: list[ExtractedFact] = []
        lines = conversation.split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Decisions
            for pattern in _DECISION_PATTERNS:
                for match in pattern.finditer(line):
                    facts.append(
                        ExtractedFact(
                            content=match.group(0).strip(),
                            fact_type="decision",
                            confidence=0.8,
                        )
                    )

            # Preferences
            for pattern in _PREFERENCE_PATTERNS:
                for match in pattern.finditer(line):
                    facts.append(
                        ExtractedFact(
                            content=match.group(0).strip(),
                            fact_type="preference",
                            confidence=0.7,
                        )
                    )

            # Events
            for pattern in _EVENT_PATTERNS:
                for match in pattern.finditer(line):
                    facts.append(
                        ExtractedFact(
                            content=match.group(0).strip(),
                            fact_type="event",
                            confidence=0.7,
                        )
                    )

            # Actions
            for pattern in _ACTION_PATTERNS:
                for match in pattern.finditer(line):
                    facts.append(
                        ExtractedFact(
                            content=match.group(0).strip(),
                            fact_type="action",
                            confidence=0.75,
                        )
                    )

            # Entity extraction: emails, URLs, @mentions
            entities: list[str] = []
            entities.extend(_EMAIL_RE.findall(line))
            entities.extend(_URL_RE.findall(line))
            entities.extend(f"@{m}" for m in _MENTION_RE.findall(line))

            if entities:
                facts.append(
                    ExtractedFact(
                        content=line[:120],
                        fact_type="entity",
                        confidence=0.9,
                        entities=entities,
                    )
                )

        return self._deduplicate(facts)

    @staticmethod
    def _deduplicate(facts: list[ExtractedFact]) -> list[ExtractedFact]:
        """Remove near-duplicate facts based on normalized content prefix."""
        seen: set[str] = set()
        unique: list[ExtractedFact] = []
        for fact in facts:
            key = fact.content.lower().strip()
            if key not in seen:
                seen.add(key)
                unique.append(fact)
        return unique
