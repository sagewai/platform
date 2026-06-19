# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""InMemoryTaskStore lease/attempts + renew + reap parity with the durable store."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sagewai.fleet.dispatcher import InMemoryTaskStore, TaskStore


def _past(store, run_id):
    """Force a claimed task's lease into the past (deterministic, no clock dep)."""
    store._claimed[run_id]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)


@pytest.mark.asyncio
async def test_claim_sets_lease_and_attempts():
    s = InMemoryTaskStore(lease_ttl_seconds=60.0, max_attempts=3)
    assert isinstance(s, TaskStore)  # Protocol still satisfied after it grows
    await s.enqueue({"run_id": "r1", "org_id": "o", "project_id": None, "pool": "default"})
    t = await s.claim_task("w", "o", [], "default", {}, project_id=None)
    assert t and s._claimed["r1"]["attempts"] == 1
    assert s._claimed["r1"]["lease_expires_at"] > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_renew_extends_only_that_worker():
    s = InMemoryTaskStore(lease_ttl_seconds=60.0)
    await s.enqueue({"run_id": "r1", "org_id": "o", "project_id": None, "pool": "default"})
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    _past(s, "r1")
    n = await s.renew_worker_leases("w")
    assert n == 1 and s._claimed["r1"]["lease_expires_at"] > datetime.now(timezone.utc)
    assert await s.renew_worker_leases("other") == 0


@pytest.mark.asyncio
async def test_reap_requeues_then_fails_at_cap():
    s = InMemoryTaskStore(lease_ttl_seconds=60.0, max_attempts=2)
    await s.enqueue({"run_id": "r1", "org_id": "o", "project_id": None, "pool": "default"})
    # attempt 1: claim, force-expire, reap -> requeued (pending, lease cleared)
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    _past(s, "r1")
    assert await s.reap_expired_leases() == {"failed": 0, "requeued": 1}
    assert (await s.get_task("r1", org_id="o", project_id=None))["status"] == "pending"
    # attempt 2: claim again (attempts now 2 == max), expire, reap -> failed
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    _past(s, "r1")
    assert await s.reap_expired_leases() == {"failed": 1, "requeued": 0}
    done = await s.get_task("r1", org_id="o", project_id=None)
    assert done["status"] == "failed" and done["error"]


@pytest.mark.asyncio
async def test_report_clears_lease():
    s = InMemoryTaskStore()
    await s.enqueue({"run_id": "r1", "org_id": "o", "project_id": None, "pool": "default"})
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    await s.report_task("r1", "completed", "out", None, worker_id="w")
    assert s._completed["r1"]["lease_expires_at"] is None
