# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Semantic fact extraction strategy for memory."""

from __future__ import annotations

import json
import logging
from typing import Any

from sagewai.memory.strategies.base import (
    ExtractedRecord,
    TurnEvent,
    strip_code_fence,
)

logger = logging.getLogger(__name__)

_PROMPT = (
    "Extract factual statements about the user (preferences, identity, location, "
    "occupation, relationships). Return ONLY a JSON array of short strings. "
    "If no facts, return []."
)


class SemanticFactStrategy:
    name = "semantic"
    namespace = "semantic"

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def extract(self, turns: list[TurnEvent]) -> list[ExtractedRecord]:
        if not turns:
            return []
        transcript = "\n".join(f"{t.role}: {t.content}" for t in turns)
        resp = await self._llm.acompletion(
            messages=[
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": transcript},
            ],
        )
        raw = resp["choices"][0]["message"]["content"].strip()
        try:
            facts = json.loads(strip_code_fence(raw))
        except json.JSONDecodeError:
            logger.warning("semantic strategy: non-JSON response: %r", raw[:200])
            return []
        if not isinstance(facts, list):
            return []
        sid = turns[0].session_id
        return [
            ExtractedRecord(namespace=self.namespace, content=str(f), source_session=sid, strategy=self.name)
            for f in facts
            if isinstance(f, str) and f.strip()
        ]
