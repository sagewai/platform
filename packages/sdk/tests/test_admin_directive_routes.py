# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for /api/v1/admin/directives — Sealed-v admin REST endpoints."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """Use a tmp admin-state.json file scoped to the test."""
    path = tmp_path / "admin-state.json"
    path.write_text(json.dumps({"workflows": {}}))

    from sagewai.admin import state_file as state_file_module

    monkeypatch.setattr(
        state_file_module, "_DEFAULT_STATE_FILE", path,
    )
    return path


@pytest.fixture
async def client(state_file):
    from sagewai.admin import directive_routes
    from sagewai.admin.state_file import AdminStateFile

    app = FastAPI()
    sf = AdminStateFile()
    directive_routes.register(app, sf)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test",
    ) as cl:
        yield cl


@pytest.mark.asyncio
async def test_list_policies_returns_default_alert_only(client):
    res = await client.get("/api/v1/admin/directives/policies")
    assert res.status_code == 200
    data = res.json()
    ids = {p["id"] for p in data["system_policies"]}
    assert ids == {
        "cost-overrun-default",
        "capability-gap-default",
        "rotation-drift-default",
    }


@pytest.mark.asyncio
async def test_put_policies_replaces_tree_atomically(client):
    body = {
        "system_policies": [
            {
                "id": "cost-overrun-default",
                "name": "Strict",
                "description": "",
                "enabled": True,
                "condition": {
                    "signal_kind": "cost_overrun",
                    "severity_at_least": None,
                    "evidence_match": {},
                },
                "action": {
                    "kind": "abort_run",
                    "target_mode": None,
                    "suggested_profile_field": None,
                    "severity": "warning",
                    "message_template": None,
                },
                "requires_approval": True,
                "rate_limit_per_run": 1,
            }
        ],
        "project_policies": {},
        "workflow_policies": {},
        "profile_suggestions": {},
        "evaluator_settings": {
            "max_signals_per_poll": 50,
            "audit_retention_days": 365,
            "approval_default_ttl_seconds": 3600,
        },
    }
    res = await client.put("/api/v1/admin/directives/policies", json=body)
    assert res.status_code == 200, res.text
    res2 = await client.get("/api/v1/admin/directives/policies")
    assert res2.json()["system_policies"][0]["action"]["kind"] == "abort_run"


@pytest.mark.asyncio
async def test_preview_resolves_cascade(client):
    res = await client.get(
        "/api/v1/admin/directives/preview",
        params={"workflow": "wf", "project_id": "p1"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "active_policies" in data
    # Defaults should resolve at the system level for any workflow.
    assert len(data["active_policies"]) >= 1


@pytest.mark.asyncio
async def test_evaluations_list_endpoint_empty_without_postgres(client):
    res = await client.get(
        "/api/v1/admin/directives/evaluations",
        params={"limit": 10},
    )
    assert res.status_code == 200
    assert res.json() == {"events": []}


@pytest.mark.asyncio
async def test_approve_returns_503_without_postgres(client):
    res = await client.post(
        "/api/v1/admin/directives/approvals/dec-bogus/approve",
        json={"actor": "ops", "note": ""},
    )
    assert res.status_code == 503
