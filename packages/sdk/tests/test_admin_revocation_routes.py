# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for /api/v1/admin/sealed/revocations routes."""
from __future__ import annotations

import os

import httpx
import pytest
from fastapi import FastAPI

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_DATABASE_URL"),
    reason="SAGEWAI_DATABASE_URL not set",
)


@pytest.fixture
async def admin_client():
    from sagewai.admin import revocation_routes
    from sagewai.core.stores.postgres import PostgresStore

    store = PostgresStore(database_url=os.environ["SAGEWAI_DATABASE_URL"])
    await store.initialize()
    await store._pool.execute("DELETE FROM sealed_revocations")

    app = FastAPI()
    revocation_routes.register(app, store=store)
    revocation_routes._REVOKE_HISTORY.clear()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await store._pool.execute("DELETE FROM sealed_revocations")
    await store.close()


@pytest.mark.asyncio
async def test_post_revocation_creates_row(admin_client):
    res = await admin_client.post(
        "/api/v1/admin/sealed/revocations",
        json={"profile_id": "acme", "secret_key": "K", "reason": "leaked"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert len(body["revocations"]) == 1
    assert body["affected_runs"] == []


@pytest.mark.asyncio
async def test_post_revocation_409_on_duplicate(admin_client):
    await admin_client.post(
        "/api/v1/admin/sealed/revocations",
        json={"profile_id": "acme", "secret_key": "K", "reason": "r"},
    )
    res = await admin_client.post(
        "/api/v1/admin/sealed/revocations",
        json={"profile_id": "acme", "secret_key": "K", "reason": "r2"},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_get_revocations(admin_client):
    await admin_client.post(
        "/api/v1/admin/sealed/revocations",
        json={"profile_id": "acme", "secret_key": "K1", "reason": "r"},
    )
    res = await admin_client.get("/api/v1/admin/sealed/revocations")
    assert res.status_code == 200
    assert len(res.json()) == 1


@pytest.mark.asyncio
async def test_delete_revocation_lifts(admin_client):
    res = await admin_client.post(
        "/api/v1/admin/sealed/revocations",
        json={"profile_id": "acme", "secret_key": "K", "reason": "r"},
    )
    rid = res.json()["revocations"][0]["id"]
    res = await admin_client.delete(f"/api/v1/admin/sealed/revocations/{rid}")
    assert res.status_code == 200
    assert res.json()["lifted_at"] is not None


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_404(admin_client):
    res = await admin_client.delete("/api/v1/admin/sealed/revocations/999999")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_delete_already_lifted_returns_409(admin_client):
    res = await admin_client.post(
        "/api/v1/admin/sealed/revocations",
        json={"profile_id": "acme", "secret_key": "K", "reason": "r"},
    )
    rid = res.json()["revocations"][0]["id"]
    await admin_client.delete(f"/api/v1/admin/sealed/revocations/{rid}")
    res = await admin_client.delete(f"/api/v1/admin/sealed/revocations/{rid}")
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_preview_returns_affected_runs(admin_client):
    """Preview reads affected runs without creating a revocation."""
    from sagewai.core.stores.postgres import PostgresStore

    store = PostgresStore(database_url=os.environ["SAGEWAI_DATABASE_URL"])
    await store.initialize()
    try:
        await store._pool.execute(
            """
            INSERT INTO workflow_runs
              (id, workflow_name, run_id, status, security_profile_ref,
               effective_env_keys, effective_secret_keys)
            VALUES ('wf:r-prev', 'wf', 'r-prev', 'running', 'acme',
                    ARRAY['K1'], ARRAY['K1'])
            ON CONFLICT (id) DO NOTHING
            """,
        )
        res = await admin_client.get(
            "/api/v1/admin/sealed/revocations/preview",
            params={"profile_id": "acme", "secret_key": "K1"},
        )
        assert res.status_code == 200
        assert "r-prev" in res.json()["affected_runs"]

        # No revocation row created
        rows = await store._pool.fetch("SELECT id FROM sealed_revocations")
        assert len(rows) == 0
    finally:
        await store._pool.execute("DELETE FROM workflow_runs WHERE run_id = 'r-prev'")
        await store.close()
