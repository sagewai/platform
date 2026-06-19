# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Ownership + idempotency for InMemoryTaskStore.report_task."""
from __future__ import annotations

import pytest

from sagewai.fleet.dispatcher import InMemoryTaskStore, NotTaskOwnerError


async def _claim_one(store, worker_id, model="gpt-4o"):
    return await store.claim_task(
        worker_id=worker_id, org_id="o", models_canonical=[model],
        pool="default", labels={},
    )


@pytest.mark.asyncio
async def test_owner_can_report():
    store = InMemoryTaskStore()
    await store.enqueue({"run_id": "r1", "model": "gpt-4o", "pool": "default"})
    await _claim_one(store, "w1")
    await store.report_task("r1", "completed", "ok", None, worker_id="w1")
    assert store._completed["r1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_non_owner_rejected():
    store = InMemoryTaskStore()
    await store.enqueue({"run_id": "r1", "model": "gpt-4o", "pool": "default"})
    await _claim_one(store, "w1")
    with pytest.raises(NotTaskOwnerError):
        await store.report_task("r1", "completed", "ok", None, worker_id="w2")


@pytest.mark.asyncio
async def test_duplicate_same_worker_status_is_idempotent():
    store = InMemoryTaskStore()
    await store.enqueue({"run_id": "r1", "model": "gpt-4o", "pool": "default"})
    await _claim_one(store, "w1")
    await store.report_task("r1", "completed", "ok", None, worker_id="w1")
    # lost-ack retry: same worker + status → no error
    await store.report_task("r1", "completed", "ok", None, worker_id="w1")
    assert store._completed["r1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_unknown_run_rejected():
    store = InMemoryTaskStore()
    with pytest.raises(NotTaskOwnerError):
        await store.report_task("nope", "completed", None, None, worker_id="w1")
