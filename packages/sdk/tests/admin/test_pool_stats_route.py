# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Admin GET /api/v1/admin/fleet/workers/{id}/pool-stats route."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_app(tmp_path):
    """Build the full admin ASGI app with an isolated state file."""
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=tmp_path / "state.json")
    return create_admin_serve_app(sf)


@pytest.fixture
def client(admin_app):
    return TestClient(admin_app)


def test_pool_stats_route_returns_404_for_unknown_worker(client):
    res = client.get("/api/v1/admin/fleet/workers/does-not-exist/pool-stats")
    assert res.status_code == 404


def test_pool_stats_route_returns_snapshot_after_heartbeat(client):
    # Register a worker
    reg = client.post(
        "/api/v1/fleet/register",
        json={"name": "w-test", "org_id": "default"},
    )
    assert reg.status_code in (200, 201), reg.text
    worker_id = reg.json()["worker_id"]

    # Approve it (heartbeat with pool_stats from an unapproved worker still works
    # because InMemoryFleetRegistry.heartbeat silently ignores unknown workers;
    # we approve here to keep the test self-consistent)
    client.post(f"/api/v1/fleet/workers/{worker_id}/approve")

    snap = {
        "worker_id": worker_id,
        "captured_at": "2026-04-26T12:00:00+00:00",
        "per_tuple": [],
        "aggregate": {
            "warm_count": 3,
            "warm_max_global": 16,
            "active_count": 1,
            "hit_rate_1h": 0.85,
            "last_evict_at": None,
        },
    }
    hb = client.post(
        "/api/v1/fleet/heartbeat",
        json={"worker_id": worker_id, "pool_stats": snap},
    )
    assert hb.status_code == 200, hb.text

    res = client.get(f"/api/v1/admin/fleet/workers/{worker_id}/pool-stats")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["aggregate"]["warm_count"] == 3
    assert body["aggregate"]["hit_rate_1h"] == pytest.approx(0.85)


def test_pool_stats_route_returns_null_snapshot_when_no_stats_sent(client):
    """Worker exists but has never sent pool_stats — route returns null snapshot."""
    reg = client.post(
        "/api/v1/fleet/register",
        json={"name": "w-nostats", "org_id": "default"},
    )
    assert reg.status_code in (200, 201), reg.text
    worker_id = reg.json()["worker_id"]

    res = client.get(f"/api/v1/admin/fleet/workers/{worker_id}/pool-stats")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {"snapshot": None}
