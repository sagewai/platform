# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""First-class project_id matching in InMemoryTaskStore.claim_task."""
from __future__ import annotations

import pytest

from sagewai.fleet.dispatcher import InMemoryTaskStore


async def _claim(store, *, org, project):
    return await store.claim_task(
        worker_id="w", org_id=org, project_id=project,
        models_canonical=["gpt-4o"], pool="default", labels={},
    )


@pytest.mark.asyncio
async def test_claim_strict_project_equality():
    store = InMemoryTaskStore()
    store.enqueue({"run_id": "rA", "org_id": "o", "project_id": "pa", "model": "gpt-4o", "pool": "default"})
    # different project -> no match
    assert await _claim(store, org="o", project="pb") is None
    # org-global worker (None) can't take a project task
    assert await _claim(store, org="o", project=None) is None
    # same project -> match
    t = await _claim(store, org="o", project="pa")
    assert t and t["run_id"] == "rA"


@pytest.mark.asyncio
async def test_org_global_task_matches_org_global_worker():
    store = InMemoryTaskStore()
    store.enqueue({"run_id": "rG", "org_id": "o", "project_id": None, "model": "gpt-4o", "pool": "default"})
    assert await _claim(store, org="o", project="pa") is None      # project worker
    t = await _claim(store, org="o", project=None)                  # org-global worker
    assert t and t["run_id"] == "rG"
