# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for gateway token management API."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.gateway.api import create_gateway_router
from sagewai.gateway.manager import TokenManager
from sagewai.gateway.store import InMemoryTokenStore


@pytest.fixture
def app():
    store = InMemoryTokenStore()
    manager = TokenManager(store=store)
    router = create_gateway_router(manager)
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_create_token(client):
    resp = client.post(
        "/gateway/tokens",
        json={"agent_name": "scout", "grantor_id": "admin-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["token"].startswith("sat-")
    assert data["token_id"]
    assert data["agent_name"] == "scout"


def test_create_token_with_options(client):
    resp = client.post(
        "/gateway/tokens",
        json={
            "agent_name": "scout",
            "grantor_id": "admin-1",
            "scopes": ["chat", "dream"],
            "single_use": True,
            "expires_in_seconds": 7200,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["token"].startswith("sat-")


def test_list_tokens(client):
    client.post("/gateway/tokens", json={"agent_name": "scout", "grantor_id": "a"})
    client.post("/gateway/tokens", json={"agent_name": "writer", "grantor_id": "a"})
    resp = client.get("/gateway/tokens")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_tokens_by_agent(client):
    client.post("/gateway/tokens", json={"agent_name": "scout", "grantor_id": "a"})
    client.post("/gateway/tokens", json={"agent_name": "writer", "grantor_id": "a"})
    resp = client.get("/gateway/tokens?agent_name=scout")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_revoke_token(client):
    create_resp = client.post(
        "/gateway/tokens",
        json={"agent_name": "scout", "grantor_id": "admin-1"},
    )
    token_id = create_resp.json()["token_id"]
    resp = client.post(f"/gateway/tokens/{token_id}/revoke")
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"


def test_delete_token(client):
    create_resp = client.post(
        "/gateway/tokens",
        json={"agent_name": "scout", "grantor_id": "admin-1"},
    )
    token_id = create_resp.json()["token_id"]
    resp = client.delete(f"/gateway/tokens/{token_id}")
    assert resp.status_code == 200

    list_resp = client.get("/gateway/tokens")
    assert len(list_resp.json()) == 0
