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

from typing import TYPE_CHECKING

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
