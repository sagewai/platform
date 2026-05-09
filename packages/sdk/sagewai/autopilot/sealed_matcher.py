# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sealed profile matching for autopilot agent steps.

:func:`match_profile` selects the best Sealed profile from a pool for a
given set of required scopes. The invariant is **strict superset**: the
profile's :attr:`ProfileRecord.granted_scopes` must be a superset of the
required scopes. When multiple profiles qualify, the one with the oldest
:attr:`ProfileRecord.last_used_at` timestamp (LRU — Least Recently Used)
is preferred so credential rotation is load-balanced across profiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    """Lightweight profile descriptor for matching purposes.

    Attributes:
        id:              Unique profile identifier.
        name:            Human-readable name.
        granted_scopes:  Frozenset of scope strings this profile grants.
        last_used_at:    UTC timestamp of the profile's most recent use.
                         Defaults to epoch — a new profile is always LRU.
    """

    id: str
    name: str
    granted_scopes: frozenset[str] = field(default_factory=frozenset)
    last_used_at: datetime = field(
        default_factory=lambda: datetime(1970, 1, 1, tzinfo=timezone.utc)
    )


def match_profile(
    required_scopes: frozenset[str],
    pool: list[ProfileRecord],
) -> ProfileRecord | None:
    """Return the best matching profile from *pool* or ``None``.

    Matching rule: a profile qualifies only if its ``granted_scopes`` is a
    **superset** of ``required_scopes`` (every required scope is granted).

    Tie-break: the qualified profile with the oldest ``last_used_at``
    (LRU) is returned first so credential usage is distributed evenly
    across equally capable profiles.

    Args:
        required_scopes: Scopes that the agent step needs.
        pool:            Candidate profiles to evaluate.

    Returns:
        The best-matching :class:`ProfileRecord`, or ``None`` if no
        profile covers all required scopes.
    """
    qualified = [p for p in pool if required_scopes <= p.granted_scopes]
    if not qualified:
        return None
    return min(qualified, key=lambda p: p.last_used_at)
