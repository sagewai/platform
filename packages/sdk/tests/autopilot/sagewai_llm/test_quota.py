# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for QuotaStatus and header parsing."""

from __future__ import annotations

from sagewai.autopilot.sagewai_llm.quota import (
    QUOTA_HEADER,
    QuotaStatus,
    parse_quota_header,
)


def test_quota_header_constant():
    assert QUOTA_HEADER == "X-Sagewai-Quota"


def test_parse_full_header():
    header = "tier=anonymous;endpoint=generate;used=12;limit=50;reset=2026-05-01T00:00:00Z"
    q = parse_quota_header(header)
    assert q == QuotaStatus(
        tier="anonymous",
        endpoint="generate",
        used=12,
        limit=50,
        reset_at="2026-05-01T00:00:00Z",
    )


def test_parse_reorders_irrelevant():
    header = "limit=200;tier=free;used=7;endpoint=retrieve;reset=2026-05-01T00:00:00Z"
    q = parse_quota_header(header)
    assert q.tier == "free"
    assert q.used == 7


def test_parse_none_returns_none():
    assert parse_quota_header(None) is None


def test_parse_malformed_returns_none():
    assert parse_quota_header("garbage") is None
    assert parse_quota_header("tier=x") is None  # missing required keys


def test_parse_non_integer_count_returns_none():
    header = "tier=x;endpoint=y;used=abc;limit=50;reset=t"
    assert parse_quota_header(header) is None


def test_quota_status_remaining():
    q = QuotaStatus(
        tier="free",
        endpoint="generate",
        used=12,
        limit=200,
        reset_at="2026-05-01T00:00:00Z",
    )
    assert q.remaining == 188


def test_quota_status_is_exhausted_when_used_equals_limit():
    q = QuotaStatus(
        tier="anonymous",
        endpoint="generate",
        used=50,
        limit=50,
        reset_at="t",
    )
    assert q.is_exhausted is True


def test_quota_status_not_exhausted_when_used_less_than_limit():
    q = QuotaStatus(
        tier="anonymous",
        endpoint="generate",
        used=49,
        limit=50,
        reset_at="t",
    )
    assert q.is_exhausted is False
