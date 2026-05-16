# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Summary extraction strategy for memory."""

from __future__ import annotations

from typing import Any

from sagewai.memory.strategies.base import ExtractedRecord, TurnEvent

_PROMPT = "Summarise the conversation below in 1-2 sentences. No preamble."


class SummaryStrategy:
    name = "summary"
    namespace = "summaries"

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
        text = resp["choices"][0]["message"]["content"].strip()
        if not text:
            return []
        sid = turns[0].session_id
        return [
            ExtractedRecord(namespace=self.namespace, content=text, source_session=sid, strategy=self.name)
        ]
