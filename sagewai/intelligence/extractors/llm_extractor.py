# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LLM-based entity and relation extraction (backward-compatible wrapper).

Provides the same extraction interface as the local GLiNER backends but
delegates to an LLM via ``litellm.acompletion``.  This is the fallback
when GLiNER is not installed.
"""

from __future__ import annotations

import json
import logging

from sagewai.intelligence.models import ExtractionResult, RelationTriple

logger = logging.getLogger(__name__)


class LLMEntityExtractor:
    """Entity extraction via LLM prompt.

    Uses ``litellm.acompletion`` to extract named entities with types and
    character offsets.  Results are non-deterministic and depend on the
    chosen model's quality.

    Args:
        model: LiteLLM model name (e.g. ``"gpt-4o-mini"``).
    """

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self._model = model

    async def extract(
        self,
        text: str,
        entity_types: list[str] | None = None,
    ) -> list[ExtractionResult]:
        """Extract entities using an LLM prompt.

        Args:
            text: Source text.
            entity_types: Optional list of entity type labels to extract.

        Returns:
            List of :class:`ExtractionResult`.
        """
        if not text.strip():
            return []

        import litellm

        types_hint = ""
        if entity_types:
            types_hint = f" Focus on these entity types: {', '.join(entity_types)}."

        prompt = (
            "Extract named entities from this text. Return a JSON array of objects "
            'with keys: "text" (entity text as it appears), "label" (entity type in '
            'UPPERCASE, e.g. PERSON, ORG, TECHNOLOGY), "start" (character offset), '
            '"end" (character offset), "confidence" (0.0-1.0).'
            f"{types_hint}\n\n"
            f"Text: {text}\n\nJSON:"
        )

        response = await litellm.acompletion(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )

        try:
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(
                    lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                )
            parsed = json.loads(raw)
            return [
                ExtractionResult(
                    text=e.get("text", ""),
                    label=e.get("label", "ENTITY"),
                    start=e.get("start", 0),
                    end=e.get("end", 0),
                    confidence=e.get("confidence", 0.5),
                )
                for e in parsed
                if isinstance(e, dict) and e.get("text")
            ]
        except (json.JSONDecodeError, TypeError, ValueError, KeyError):
            logger.warning("Failed to parse LLM entity extraction for: %s", text[:200])
            return []


class LLMRelationExtractor:
    """Relation extraction via LLM prompt (backward-compatible pattern).

    Mirrors the existing ``_extract_relations()`` function in
    ``sagewai/memory/nebula.py`` but wraps the result as
    :class:`RelationTriple` instances.

    Args:
        model: LiteLLM model name (e.g. ``"gpt-4o-mini"``).
    """

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self._model = model

    async def extract(self, text: str) -> list[RelationTriple]:
        """Extract relation triples using an LLM prompt.

        Returns:
            List of :class:`RelationTriple`.
        """
        if not text.strip():
            return []

        import litellm

        prompt = (
            "Extract entity-relationship triples from this text. "
            "Return as JSON array of [subject, predicate, object] arrays. "
            'Example: [["Python", "is_a", "Language"]]\n\n'
            f"Text: {text}\n\nJSON:"
        )

        response = await litellm.acompletion(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )

        try:
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(
                    lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                )
            parsed = json.loads(raw)
            return [
                RelationTriple(
                    subject=s,
                    predicate=p,
                    object=o,
                    source_text=text[:200],
                )
                for s, p, o in parsed
                if isinstance(s, str) and isinstance(p, str) and isinstance(o, str)
            ]
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning(
                "Failed to parse LLM relation extraction for: %s", text[:200]
            )
            return []
