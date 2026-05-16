# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Slot extractor Protocol and rule-based stub implementation.

Plan 3 ships a *rule-based stub* that parses ``key=value`` and
``key="quoted value"`` patterns from the raw goal string.  This is
intentionally simple: it makes Plan 3 fully testable without any LLM
calls and gives Plan 4 a clean `SlotExtractor` Protocol to swap in the
real LLM-backed extractor.

Usage (Plan 4 will replace the extractor at construction time)::

    extractor = RuleBasedExtractor()
    slots = extractor.extract("topic=AI limit=10", slot_names=["topic", "limit"])
    # → {"topic": "AI", "limit": "10"}
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SlotExtractor(Protocol):
    """Protocol for objects that extract slot values from a goal string.

    Implementations may be synchronous (rule-based stub) or wrap an
    async LLM call via ``asyncio.run`` — that is an implementation
    detail. From the router's point of view, ``extract`` is synchronous
    so that it can be called from sync helpers inside ``GoalRouter``.
    """

    def extract(
        self,
        goal: str,
        *,
        slot_names: list[str],
    ) -> dict[str, Any]:
        """Extract slot values from *goal*.

        Args:
            goal: The raw user goal string.
            slot_names: The names of slots to attempt extraction for.
                Only these keys appear in the returned dict.

        Returns:
            A dict with exactly the keys in *slot_names*.  Values that
            could not be extracted are ``None``.
        """
        ...


# ─── quoted-value pattern: key="some value with spaces"
_QUOTED_RE = re.compile(r'(\w+)="([^"]*)"')
# ─── unquoted-value pattern: key=value (stops at whitespace)
_UNQUOTED_RE = re.compile(r"(\w+)=(\S+)")


class RuleBasedExtractor:
    """Slot extractor that parses ``key=value`` patterns from the goal string.

    Parsing rules (applied left-to-right, later assignments win):

    1. ``key="quoted value"`` — value includes everything between the
       double quotes, including spaces and ``=`` signs.
    2. ``key=unquoted`` — value runs until the next whitespace character.

    Both patterns are case-sensitive. Values are always returned as raw
    strings; type coercion is the caller's responsibility.
    """

    def extract(
        self,
        goal: str,
        *,
        slot_names: list[str],
    ) -> dict[str, Any]:
        if not slot_names:
            return {}

        # Collect all assignments from goal, later wins on duplicate keys.
        assignments: dict[str, str] = {}
        # Pass 1: quoted values (must come first to avoid partial matches).
        for key, val in _QUOTED_RE.findall(goal):
            assignments[key] = val
        # Pass 2: unquoted values for keys not already captured as quoted.
        for key, val in _UNQUOTED_RE.findall(goal):
            # Skip keys whose value contains `"` — they were quoted.
            if '"' not in val:
                assignments[key] = val

        return {name: assignments.get(name) for name in slot_names}
