# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for GET /api/v1/autopilot/fleet/workers — Plan I Task 2."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from sagewai.admin.autopilot_routes import create_autopilot_router
from sagewai.admin.state_file import AdminStateFile
from sagewai.fleet.models import WorkerApprovalStatus, WorkerCapabilities, WorkerRecord


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def sf(tmp_path):
    return AdminStateFile(tmp_path / "state.json")


@pytest.fixture()
def authenticated_sf(sf):
    sf.complete_setup(
        org_name="Test Org",
        admin_email="admin@example.com",
        admin_password="hunter2",
    )
    result = sf.validate_login("admin@example.com", "hunter2")
    assert result is not None
    return sf, result["access_token"]


@pytest.fixture()
def app_and_sf(authenticated_sf):
    sf, _token = authenticated_sf
    app = FastAPI()
    app.include_router(create_autopilot_router(sf), prefix="/api/v1")
    return app, sf


@pytest.fixture()
def auth_headers(authenticated_sf):
    _sf, token = authenticated_sf
    return {"Authorization": f"Bearer {token}"}


# ── worker helpers ────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)


def _worker(wid: str, pool: str = "default", *, probe_status: str = "healthy") -> WorkerRecord:
    return WorkerRecord(
        id=wid,
        name=f"worker-{wid}",
        org_id="org1",
        capabilities=WorkerCapabilities(
            models_canonical=["gpt-4o"],
            labels={"web_search": "true"},
            pool=pool,
        ),
        approval_status=WorkerApprovalStatus.APPROVED,
        registered_at=_NOW,
        probe_status=probe_status,
    )


# ── tests ─────────────────────────────────────────────────────────────


async def test_list_workers_returns_two_workers(app_and_sf, auth_headers):
    app, _sf = app_and_sf
    workers = [_worker("w1"), _worker("w2", probe_status="degraded")]

    with patch(
        "sagewai.admin.autopilot_routes._get_fleet_registry_snapshot",
        return_value=workers,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as ac:
            resp = await ac.get(
                "/api/v1/autopilot/fleet/workers", headers=auth_headers
            )

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2
    ids = {w["id"] for w in body}
    assert ids == {"w1", "w2"}


async def test_list_workers_fields_present(app_and_sf, auth_headers):
    app, _sf = app_and_sf
    workers = [_worker("w1")]

    with patch(
        "sagewai.admin.autopilot_routes._get_fleet_registry_snapshot",
        return_value=workers,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as ac:
            resp = await ac.get(
                "/api/v1/autopilot/fleet/workers", headers=auth_headers
            )

    body = resp.json()
    w = body[0]
    assert "id" in w
    assert "name" in w
    assert "models_canonical" in w
    assert "pool" in w
    assert "probe_status" in w


async def test_list_workers_unauthenticated(app_and_sf):
    app, _sf = app_and_sf
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as ac:
        resp = await ac.get("/api/v1/autopilot/fleet/workers")
    assert resp.status_code == 401
