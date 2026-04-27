# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""FleetRegistry caches PoolStatsSnapshot from heartbeat."""
from __future__ import annotations

import pytest

from sagewai.fleet.registry import InMemoryFleetRegistry
from sagewai.fleet.models import WorkerCapabilities


@pytest.mark.asyncio
async def test_in_memory_heartbeat_stores_pool_stats():
    registry = InMemoryFleetRegistry()
    worker = await registry.register_worker(
        org_id="org-1",
        name="w-1",
        capabilities=WorkerCapabilities(),
    )
    await registry.approve_worker(worker.id, approved_by="admin")

    snap = {"worker_id": worker.id, "captured_at": "2026-04-26T12:00:00+00:00",
            "per_tuple": [], "aggregate": {"warm_count": 2, "warm_max_global": 16,
                                            "active_count": 1, "hit_rate_1h": 0.7,
                                            "last_evict_at": None}}
    await registry.heartbeat(worker.id, pool_stats=snap)
    cached = await registry.get_pool_stats(worker.id)
    assert cached == snap


@pytest.mark.asyncio
async def test_in_memory_heartbeat_without_pool_stats_keeps_cached_value():
    registry = InMemoryFleetRegistry()
    worker = await registry.register_worker(
        org_id="org-1",
        name="w-1",
        capabilities=WorkerCapabilities(),
    )
    await registry.approve_worker(worker.id, approved_by="admin")

    snap = {"worker_id": worker.id, "captured_at": "2026-04-26T12:00:00+00:00",
            "per_tuple": [], "aggregate": {"warm_count": 2, "warm_max_global": 16,
                                            "active_count": 0, "hit_rate_1h": None,
                                            "last_evict_at": None}}
    await registry.heartbeat(worker.id, pool_stats=snap)
    await registry.heartbeat(worker.id)  # no pool_stats
    cached = await registry.get_pool_stats(worker.id)
    # Heartbeat without pool_stats does NOT clear the cache.
    assert cached == snap


@pytest.mark.asyncio
async def test_get_pool_stats_returns_none_for_unknown_worker():
    registry = InMemoryFleetRegistry()
    cached = await registry.get_pool_stats("unknown")
    assert cached is None
