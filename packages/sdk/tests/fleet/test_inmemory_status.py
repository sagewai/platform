# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""InMemoryTaskStore: async enqueue + get_task/list_tasks + Protocol parity."""
from __future__ import annotations

import pytest

from sagewai.fleet.dispatcher import InMemoryTaskStore, TaskStore


@pytest.mark.asyncio
async def test_async_enqueue_and_status_views():
    s = InMemoryTaskStore()
    assert isinstance(s, TaskStore)  # Protocol still satisfied after it grows
    await s.enqueue({"run_id": "r1", "org_id": "o", "project_id": None, "pool": "default"})
    got = await s.get_task("r1", org_id="o", project_id=None)
    assert got and got["run_id"] == "r1" and got["status"] == "pending"
    # scope mismatch -> None
    assert await s.get_task("r1", org_id="other", project_id=None) is None
    listed = await s.list_tasks(org_id="o", project_id=None)
    assert [t["run_id"] for t in listed] == ["r1"]


@pytest.mark.asyncio
async def test_status_reflects_claim_and_report():
    s = InMemoryTaskStore()
    await s.enqueue({"run_id": "r1", "org_id": "o", "project_id": None, "pool": "default"})
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    assert (await s.get_task("r1", org_id="o", project_id=None))["status"] == "claimed"
    await s.report_task("r1", "completed", "out", None, worker_id="w")
    done = await s.get_task("r1", org_id="o", project_id=None)
    assert done["status"] == "completed" and done["output"] == "out"


@pytest.mark.asyncio
async def test_report_rejects_non_terminal_status():
    """Parity with PostgresTaskStore: only completed/failed may be reported."""
    s = InMemoryTaskStore()
    await s.enqueue({"run_id": "r1", "org_id": "o", "project_id": None, "pool": "default"})
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    with pytest.raises(ValueError):
        await s.report_task("r1", "pending", None, None, worker_id="w")
    assert (await s.get_task("r1", org_id="o", project_id=None))["status"] == "claimed"
