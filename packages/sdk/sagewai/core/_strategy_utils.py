# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Shared utilities for execution strategies."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sagewai.models.message import ChatMessage


def parse_score(text: str, min_val: float = 1.0, max_val: float = 10.0) -> float:
    """Extract a numeric score from LLM evaluation response.

    Scans tokens for the first parseable float and clamps to [min_val, max_val].
    Returns the midpoint if no number is found.
    """
    text = text.strip()
    for token in text.split():
        cleaned = token.strip(".,;:()[]")
        try:
            score = float(cleaned)
            return max(min_val, min(max_val, score))
        except ValueError:
            continue
    return (min_val + max_val) / 2


def extract_task(messages: list[ChatMessage]) -> str:
    """Extract the original user task from a message list."""
    for msg in messages:
        if msg.role.value == "user" and msg.content:
            return msg.content
    return ""


def parse_json(text: str) -> Any:
    """Parse JSON from an LLM response, tolerant of SLM formatting quirks.

    Cheap and local models routinely deviate from "return only JSON":
    they wrap output in a Markdown fence, add a prose preamble, or both.
    This parser (1) extracts a fenced block if present, (2) tries a direct
    parse, then (3) falls back to the outermost ``[...]`` or ``{...}``
    substring. Raises ``json.JSONDecodeError`` if no JSON can be recovered.
    """
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = s.find(open_ch)
        end = s.rfind(close_ch)
        if start != -1 and end > start:
            return json.loads(s[start : end + 1])
    raise json.JSONDecodeError("no JSON object found in text", s, 0)
