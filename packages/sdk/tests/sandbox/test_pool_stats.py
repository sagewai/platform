# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PoolStatsRecord ring buffer + snapshot serialisation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.models import NetworkPolicy, SandboxImageVariant, SandboxMode
from sagewai.sandbox.pool_protocol import PoolKey
from sagewai.sandbox.pool_stats import (
    AggregateStats,
    PerTupleStats,
    PoolStatsRecord,
    PoolStatsSnapshot,
)


def _key() -> PoolKey:
    return PoolKey(
        image_digest="sha256:abc",
        sandbox_mode=SandboxMode.PER_RUN,
        execution_mode=ExecutionMode.SANDBOXED,
        network_policy=NetworkPolicy.NONE,
        image_variant=SandboxImageVariant.BASE,
    )


def test_record_starts_with_zero_counts() -> None:
    rec = PoolStatsRecord()
    assert rec.warm_count == 0
    assert rec.active_count == 0
    assert rec.hits_total == 0
    assert rec.misses_total == 0
    assert rec.hit_rate_1h(now=datetime.now(timezone.utc)) is None


def test_record_acquire_hit_increments_hit_counters() -> None:
    rec = PoolStatsRecord()
    now = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    rec.record_acquire(hit=True, now=now)
    assert rec.hits_total == 1
    assert rec.misses_total == 0
    assert rec.hit_rate_1h(now=now) == 1.0


def test_record_acquire_miss_increments_miss_counters() -> None:
    rec = PoolStatsRecord()
    now = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    rec.record_acquire(hit=False, now=now)
    assert rec.misses_total == 1
    assert rec.hit_rate_1h(now=now) == 0.0


def test_hit_rate_mixed() -> None:
    rec = PoolStatsRecord()
    base = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    for _ in range(7):
        rec.record_acquire(hit=True, now=base)
    for _ in range(3):
        rec.record_acquire(hit=False, now=base)
    assert rec.hit_rate_1h(now=base) == pytest.approx(0.7, abs=1e-6)


def test_ring_drops_buckets_older_than_one_hour() -> None:
    rec = PoolStatsRecord()
    old = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    fresh = old + timedelta(hours=2)
    rec.record_acquire(hit=True, now=old)
    rec.record_acquire(hit=False, now=fresh)
    # Old bucket is outside the 1h window from `fresh`.
    assert rec.hit_rate_1h(now=fresh) == 0.0


def test_record_evict_updates_last_evict() -> None:
    rec = PoolStatsRecord()
    now = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    rec.record_evict(reason="idle_timeout", now=now)
    assert rec.evictions_total == 1
    assert rec.last_evict_at == now
    assert rec.last_evict_reason == "idle_timeout"


def test_snapshot_round_trip() -> None:
    snap = PoolStatsSnapshot(
        worker_id="w-1",
        captured_at=datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc),
        per_tuple=[
            PerTupleStats(
                image_variant="base",
                execution_mode="sandboxed",
                network_policy="none",
                warm_count=2,
                warm_max=4,
                active_count=1,
                hit_rate_1h=0.75,
                last_evict_at=None,
                last_evict_reason=None,
            )
        ],
        aggregate=AggregateStats(
            warm_count=2,
            warm_max_global=16,
            active_count=1,
            hit_rate_1h=0.75,
            last_evict_at=None,
        ),
    )
    j = snap.model_dump_json()
    snap2 = PoolStatsSnapshot.model_validate_json(j)
    assert snap == snap2
