# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Request complexity classifier for the LLM Harness.

Classifies incoming LLM requests into SIMPLE/MEDIUM/COMPLEX tiers
using heuristic scoring — no LLM calls, pure computation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sagewai.harness.models import ComplexityTier

logger = logging.getLogger(__name__)

# Regex patterns for complexity keyword detection
_COMPLEX_KEYWORDS = re.compile(
    r"\b(architect|design|plan|review|refactor|analyze|implement|migrate|"
    r"restructure|optimize|security|audit|multi[- ]?file|end[- ]?to[- ]?end)\b",
    re.IGNORECASE,
)
_SIMPLE_KEYWORDS = re.compile(
    r"\b(fix|typo|rename|add comment|simple|quick|small|trivial|"
    r"autocomplete|one[- ]?line|import|lint|format)\b",
    re.IGNORECASE,
)

CHARS_PER_TOKEN = 4


@dataclass
class ClassifierThresholds:
    """Configurable boundaries for tier classification.

    Scores below `simple_max` are SIMPLE, between `simple_max`
    and `complex_min` are MEDIUM, and above `complex_min` are COMPLEX.
    """

    simple_max: int = 30
    complex_min: int = 70


@dataclass
class ClassificationResult:
    """Result of request complexity classification."""

    tier: ComplexityTier
    score: int
    confidence: float
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)


class RequestClassifier:
    """Heuristic classifier that scores request complexity.

    Scoring signals (all computed from the request, no network calls):
    - Total token estimate across all messages
    - Number of messages (conversation depth)
    - System prompt size
    - Last user message length
    - Code block count
    - Tool/function definition count
    - Complexity vs simplicity keywords
    """

    def __init__(
        self,
        thresholds: ClassifierThresholds | None = None,
    ) -> None:
        self.thresholds = thresholds or ClassifierThresholds()

    def classify(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str = "",
    ) -> ClassificationResult:
        """Classify a request's complexity based on message content.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            tools: Optional tool/function definitions.
            model: The model requested by the client (informational).

        Returns:
            ClassificationResult with tier, score, confidence, and signals.
        """
        signals: dict[str, Any] = {}
        score = 50  # Start at midpoint

        # --- Signal 1: Total token estimate ---
        total_chars = sum(
            len(self._extract_text(m)) for m in messages
        )
        total_tokens = total_chars // CHARS_PER_TOKEN
        signals["total_tokens"] = total_tokens

        if total_tokens > 8000:
            score += 30
        elif total_tokens > 4000:
            score += 20
        elif total_tokens > 2000:
            score += 10
        elif total_tokens < 200:
            score -= 15

        # --- Signal 2: Message count ---
        msg_count = len(messages)
        signals["message_count"] = msg_count

        if msg_count > 20:
            score += 15
        elif msg_count > 10:
            score += 10
        elif msg_count <= 2:
            score -= 10

        # --- Signal 3: System prompt size ---
        system_text = ""
        for m in messages:
            if m.get("role") == "system":
                system_text += self._extract_text(m)
        system_tokens = len(system_text) // CHARS_PER_TOKEN
        signals["system_tokens"] = system_tokens

        if system_tokens > 4000:
            score += 15
        elif system_tokens > 2000:
            score += 10

        # --- Signal 4: Last user message length ---
        last_user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_text = self._extract_text(m)
                break
        last_user_chars = len(last_user_text)
        signals["last_user_chars"] = last_user_chars

        if last_user_chars < 50:
            score -= 20
        elif last_user_chars < 150:
            score -= 10
        elif last_user_chars > 3000:
            score += 20
        elif last_user_chars > 1000:
            score += 10

        # --- Signal 5: Code blocks ---
        all_user_text = " ".join(
            self._extract_text(m) for m in messages if m.get("role") == "user"
        )
        code_blocks = all_user_text.count("```")
        signals["code_blocks"] = code_blocks

        if code_blocks >= 6:
            score += 15
        elif code_blocks >= 3:
            score += 10

        # --- Signal 6: Tool/function count ---
        tool_count = len(tools) if tools else 0
        signals["tool_count"] = tool_count

        if tool_count > 10:
            score += 20
        elif tool_count > 5:
            score += 15
        elif tool_count > 0:
            score += 5

        # --- Signal 7: Complexity keywords ---
        complex_matches = len(_COMPLEX_KEYWORDS.findall(last_user_text))
        simple_matches = len(_SIMPLE_KEYWORDS.findall(last_user_text))
        signals["complex_keywords"] = complex_matches
        signals["simple_keywords"] = simple_matches

        score += complex_matches * 8
        score -= simple_matches * 8

        # Clamp score to [0, 100]
        score = max(0, min(100, score))
        signals["raw_score"] = score

        # Map score to tier
        if score < self.thresholds.simple_max:
            tier = ComplexityTier.SIMPLE
        elif score >= self.thresholds.complex_min:
            tier = ComplexityTier.COMPLEX
        else:
            tier = ComplexityTier.MEDIUM

        # Confidence: how far from the nearest boundary
        if tier == ComplexityTier.SIMPLE:
            distance = self.thresholds.simple_max - score
            max_distance = self.thresholds.simple_max
        elif tier == ComplexityTier.COMPLEX:
            distance = score - self.thresholds.complex_min
            max_distance = 100 - self.thresholds.complex_min
        else:
            # Medium: distance from both boundaries
            dist_simple = score - self.thresholds.simple_max
            dist_complex = self.thresholds.complex_min - score
            distance = min(dist_simple, dist_complex)
            max_distance = (self.thresholds.complex_min - self.thresholds.simple_max) / 2

        confidence = min(1.0, distance / max(max_distance, 1)) * 0.5 + 0.5

        # Build reason string
        reason_parts = []
        if total_tokens > 4000:
            reason_parts.append(f"large context ({total_tokens} tokens)")
        if tool_count > 5:
            reason_parts.append(f"{tool_count} tools")
        if complex_matches > 0:
            reason_parts.append(f"{complex_matches} complexity keywords")
        if simple_matches > 0:
            reason_parts.append(f"{simple_matches} simplicity keywords")
        if last_user_chars < 50:
            reason_parts.append("very short query")
        if msg_count > 10:
            reason_parts.append(f"{msg_count} messages")

        reason = f"Score {score} → {tier.value}"
        if reason_parts:
            reason += f" ({', '.join(reason_parts)})"

        return ClassificationResult(
            tier=tier,
            score=score,
            confidence=confidence,
            reason=reason,
            signals=signals,
        )

    @staticmethod
    def _extract_text(message: dict[str, Any]) -> str:
        """Extract text content from a message dict.

        Handles both string content and content arrays (Anthropic format).
        """
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    parts.append(part)
            return " ".join(parts)
        return str(content)
