# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Preference extraction strategy for memory."""

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
    "Extract user preferences as JSON list of {key, value} objects. "
    "Examples of keys: tone, language, format, verbosity, communication_style. "
    "If none, return []."
)


class PreferenceStrategy:
    name = "preference"
    namespace = "preferences"

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
            prefs = json.loads(strip_code_fence(raw))
        except json.JSONDecodeError:
            logger.warning("preference strategy: non-JSON response: %r", raw[:200])
            return []
        if not isinstance(prefs, list):
            return []
        sid = turns[0].session_id
        out: list[ExtractedRecord] = []
        for p in prefs:
            if not isinstance(p, dict) or "key" not in p or "value" not in p:
                continue
            out.append(
                ExtractedRecord(
                    namespace=self.namespace,
                    content=f"{p['key']}={p['value']}",
                    source_session=sid,
                    strategy=self.name,
                    metadata={"key": str(p["key"]), "value": str(p["value"])},
                )
            )
        return out
