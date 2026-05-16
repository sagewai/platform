# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pool statistics: in-memory ring buffer + JSON-serialisable snapshot."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

_RING_WINDOW = timedelta(hours=1)
_BUCKET_SIZE = timedelta(minutes=1)
_RING_BUCKETS = 60


def _floor_minute(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


@dataclass(slots=True)
class PoolStatsRecord:
    """Per-tuple counters + 1h-rolling ring buffer.

    Mutated under the pool's `asyncio.Lock`. All time math is naive in
    the sense of "wall clock the pool sees" — the ring buckets the
    `now` argument by minute.
    """

    warm_count: int = 0
    active_count: int = 0
    hits_total: int = 0
    misses_total: int = 0
    evictions_total: int = 0
    discards_after_cleanup_total: int = 0
    last_evict_at: datetime | None = None
    last_evict_reason: str | None = None
    last_acquire_at: datetime | None = None
    # Ring of (bucket_minute, hits, misses).
    _ring: deque[tuple[datetime, int, int]] = field(default_factory=lambda: deque(maxlen=_RING_BUCKETS))

    def record_acquire(self, *, hit: bool, now: datetime) -> None:
        if hit:
            self.hits_total += 1
        else:
            self.misses_total += 1
        self.last_acquire_at = now
        bucket = _floor_minute(now)
        if self._ring and self._ring[-1][0] == bucket:
            ts, h, m = self._ring[-1]
            self._ring[-1] = (ts, h + (1 if hit else 0), m + (0 if hit else 1))
        else:
            self._ring.append((bucket, 1 if hit else 0, 0 if hit else 1))

    def record_evict(self, *, reason: str, now: datetime) -> None:
        self.evictions_total += 1
        self.last_evict_at = now
        self.last_evict_reason = reason

    def record_discard_after_cleanup(self) -> None:
        self.discards_after_cleanup_total += 1

    def hit_rate_1h(self, *, now: datetime) -> float | None:
        cutoff = now - _RING_WINDOW
        hits, misses = 0, 0
        for ts, h, m in self._ring:
            if ts >= cutoff:
                hits += h
                misses += m
        total = hits + misses
        if total == 0:
            return None
        return hits / total


class PerTupleStats(BaseModel):
    image_variant: str
    execution_mode: str
    network_policy: str
    warm_count: int
    warm_max: int
    active_count: int
    hit_rate_1h: float | None = None
    last_evict_at: datetime | None = None
    last_evict_reason: str | None = None


class AggregateStats(BaseModel):
    warm_count: int
    warm_max_global: int
    active_count: int
    hit_rate_1h: float | None = None
    last_evict_at: datetime | None = None


class PoolStatsSnapshot(BaseModel):
    """JSON-serialisable shape carried over heartbeat + admin REST."""

    worker_id: str
    captured_at: datetime
    per_tuple: list[PerTupleStats] = Field(default_factory=list)
    aggregate: AggregateStats
