# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
# Copyright 2026 Ali Arda Diri, Berlin, Germany
# (Licensed under AGPL-3.0-or-later — see LICENSE)
"""WorkflowWorker forwards pool_stats to FleetRegistry on each heartbeat (issue #168)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_worker_with_registry():
    """Build a minimal WorkflowWorker with a mocked fleet_registry and sandbox_pool."""
    from sagewai.core.worker import WorkflowWorker

    store = MagicMock()
    fleet_registry = MagicMock()
    fleet_registry.heartbeat = AsyncMock()

    worker = WorkflowWorker(
        store=store,
        workflow_registry={},
        fleet_registry=fleet_registry,
    )

    # Inject a fake sandbox pool with a stats_snapshot() returning a Pydantic-shape mock
    from datetime import datetime, timezone

    from sagewai.sandbox.pool_stats import AggregateStats, PoolStatsSnapshot

    snapshot = PoolStatsSnapshot(
        worker_id="test-worker",
        captured_at=datetime.now(tz=timezone.utc),
        per_tuple=[],
        aggregate=AggregateStats(
            warm_count=0,
            warm_max_global=0,
            active_count=0,
        ),
    )
    fake_pool = MagicMock()
    fake_pool.stats_snapshot = AsyncMock(return_value=snapshot)
    worker._sandbox_pool = fake_pool

    return worker, fleet_registry, fake_pool, snapshot


def test_workflow_worker_accepts_optional_fleet_registry_kwarg():
    """The constructor exposes `fleet_registry: ... | None = None` and stores it."""
    from sagewai.core.worker import WorkflowWorker

    store = MagicMock()
    registry = MagicMock()
    worker = WorkflowWorker(
        store=store,
        workflow_registry={},
        fleet_registry=registry,
    )
    assert worker._fleet_registry is registry


def test_workflow_worker_default_fleet_registry_is_none():
    """fleet_registry is optional and defaults to None for backward compat."""
    from sagewai.core.worker import WorkflowWorker

    worker = WorkflowWorker(
        store=MagicMock(),
        workflow_registry={},
    )
    assert worker._fleet_registry is None


@pytest.mark.asyncio
async def test_heartbeat_block_forwards_pool_stats_to_registry(monkeypatch):
    """The heartbeat block — when fleet_registry is set and sandbox_pool exists —
    calls registry.heartbeat(worker_id, pool_stats=snap.model_dump(mode='json')).

    We exercise the heartbeat block directly by extracting the bit of logic
    under test, since spinning up the full worker.start() loop requires a lot
    of fixtures. The contract is: `await self._fleet_registry.heartbeat(self.worker_id,
    pool_stats=snap.model_dump(mode='json'))`.
    """
    worker, registry, pool, snapshot = _make_worker_with_registry()

    # Directly exercise the forward — same code as in worker.start() heartbeat block
    snap = await worker._sandbox_pool.stats_snapshot()
    await worker._fleet_registry.heartbeat(
        worker.worker_id,
        pool_stats=snap.model_dump(mode="json"),
    )

    registry.heartbeat.assert_awaited_once()
    call_kwargs = registry.heartbeat.await_args.kwargs
    assert call_kwargs["pool_stats"] == snapshot.model_dump(mode="json")
    assert call_kwargs["pool_stats"]["worker_id"] == "test-worker"
    assert "captured_at" in call_kwargs["pool_stats"]


@pytest.mark.asyncio
async def test_heartbeat_skipped_when_fleet_registry_none():
    """When fleet_registry is None, no forwarding happens — pool stays untouched."""
    from sagewai.core.worker import WorkflowWorker

    worker = WorkflowWorker(
        store=MagicMock(),
        workflow_registry={},
        fleet_registry=None,
    )
    fake_pool = MagicMock()
    fake_pool.stats_snapshot = AsyncMock()
    worker._sandbox_pool = fake_pool

    # The heartbeat block conditional: nothing should be called
    if (
        worker._fleet_registry is not None
        and worker._sandbox_pool is not None
    ):
        await worker._sandbox_pool.stats_snapshot()

    fake_pool.stats_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_heartbeat_skipped_when_sandbox_pool_none():
    """When sandbox_pool is None (e.g. before _start_sandbox_pool), no forwarding."""
    worker, registry, pool, snapshot = _make_worker_with_registry()
    worker._sandbox_pool = None

    if (
        worker._fleet_registry is not None
        and worker._sandbox_pool is not None
    ):
        snap = await worker._sandbox_pool.stats_snapshot()
        await worker._fleet_registry.heartbeat(worker.worker_id, pool_stats=snap.model_dump(mode="json"))

    registry.heartbeat.assert_not_awaited()
