# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Heartbeat renews leases + the FleetReaper is wired into the app lifespan."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from sagewai.db import factory
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "_home"))
    factory.reset_engine()
    yield
    factory.reset_engine()


def _app(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile
    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")
    app = create_admin_serve_app(sf)
    token = sf.validate_login("a@b.com", "pw123456")["access_token"]
    return app, token


def test_reaper_wired_in_lifespan(tmp_path):
    """The lifespan starts a FleetReaper (after the store is inited — Task 6 Step 4)."""
    app, token = _app(tmp_path)
    with TestClient(app):  # lifespan runs
        assert app.state.fleet_reaper is not None


@pytest.mark.asyncio
async def test_heartbeat_renews_lease(tmp_path):
    """The /heartbeat route calls renew_worker_leases: force the in-flight lease
    into the past, heartbeat, and a reap then finds nothing. Asserting only
    hb==200 would pass even if the renew were omitted — this asserts it ran."""
    from datetime import datetime, timezone

    import httpx
    from sqlalchemy import update

    from sagewai.db.models import FleetTaskModel

    app, token = _app(tmp_path)
    store = app.state.fleet_task_store
    admin = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as cl:
        reg = (await cl.post("/api/v1/fleet/register",
                             json={"name": "w", "models": ["gpt-4o"]}, headers=admin)).json()
        wid, secret = reg["worker_id"], reg["worker_secret"]
        await cl.post(f"/api/v1/fleet/workers/{wid}/approve", headers=admin)
        run_id = (await cl.post("/api/v1/fleet/tasks",
                                json={"model": "gpt-4o", "payload": {"m": "hi"}},
                                headers=admin)).json()["run_id"]
        wh = {"X-Worker-Id": wid, "X-Worker-Secret": secret}
        claim = await cl.post("/api/v1/fleet/claim", json={}, headers=wh)
        assert claim.status_code == 200 and claim.json()["run_id"] == run_id
        t = FleetTaskModel.__table__
        async with store._engine.begin() as conn:  # force the lease into the past
            await conn.execute(update(t).where(t.c.run_id == run_id).values(
                lease_expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        assert (await cl.post("/api/v1/fleet/heartbeat", json={}, headers=wh)).status_code == 200
    assert (await store.reap_expired_leases())["requeued"] == 0  # renewed -> not expired
