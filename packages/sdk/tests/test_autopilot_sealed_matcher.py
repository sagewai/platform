# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sealed_matcher — Plan K Task 2."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.autopilot.sealed_matcher import ProfileRecord, match_profile


_NOW = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
_OLDER = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
_NEWEST = datetime(2026, 5, 9, 18, 0, tzinfo=timezone.utc)


def _profile(pid: str, scopes: set[str], last_used: datetime = _NOW) -> ProfileRecord:
    return ProfileRecord(id=pid, name=pid, granted_scopes=frozenset(scopes), last_used_at=last_used)


# ── match_profile — no match ──────────────────────────────────────────────


def test_empty_pool_returns_none():
    assert match_profile(frozenset({"network.outbound.fetch"}), []) is None


def test_profile_missing_required_scope_returns_none():
    pool = [_profile("p1", {"fs.read"})]
    result = match_profile(frozenset({"network.outbound.fetch"}), pool)
    assert result is None


def test_partial_scope_coverage_returns_none():
    pool = [_profile("p1", {"fs.read"})]
    result = match_profile(frozenset({"fs.read", "exec.shell"}), pool)
    assert result is None


# ── match_profile — single match ──────────────────────────────────────────


def test_exact_scope_match_returns_profile():
    pool = [_profile("p1", {"fs.read"})]
    result = match_profile(frozenset({"fs.read"}), pool)
    assert result is not None
    assert result.id == "p1"


def test_superset_scope_match_returns_profile():
    """Profile with extra scopes still matches (superset >= required)."""
    pool = [_profile("p1", {"fs.read", "fs.write", "exec.shell"})]
    result = match_profile(frozenset({"fs.read"}), pool)
    assert result is not None
    assert result.id == "p1"


def test_empty_required_scopes_matches_any_profile():
    """No scope requirements → any profile matches."""
    pool = [_profile("p1", {"fs.read"}), _profile("p2", set())]
    result = match_profile(frozenset(), pool)
    assert result is not None


# ── match_profile — LRU tie-break ─────────────────────────────────────────


def test_lru_tie_break_returns_oldest_last_used():
    """When multiple profiles match, prefer the one used least recently."""
    oldest = _profile("oldest", {"network.outbound.fetch"}, last_used=_OLDER)
    newest = _profile("newest", {"network.outbound.fetch"}, last_used=_NEWEST)
    pool = [newest, oldest]  # deliberate non-chronological order
    result = match_profile(frozenset({"network.outbound.fetch"}), pool)
    assert result is not None
    assert result.id == "oldest"


def test_lru_tie_break_stable_when_same_timestamp():
    """Same last_used → stable first-match order."""
    p1 = _profile("p1", {"fs.read"}, last_used=_NOW)
    p2 = _profile("p2", {"fs.read"}, last_used=_NOW)
    result = match_profile(frozenset({"fs.read"}), [p1, p2])
    assert result is not None
    assert result.id == "p1"


# ── match_profile — requires strict superset ──────────────────────────────


def test_strict_superset_not_subset():
    """Profile must cover ALL required scopes — partial is rejected."""
    pool = [_profile("p1", {"fs.read"})]
    result = match_profile(frozenset({"fs.read", "network.outbound.fetch"}), pool)
    assert result is None
