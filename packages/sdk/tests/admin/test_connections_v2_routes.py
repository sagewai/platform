# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Generic CRUD admin routes at /api/v1/admin/connections/*.

Covers the 10 routes mounted by ``connections_v2_routes.register`` plus
the plugin extra_routes sub-mounts (oauth2's /start endpoint).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin import connections_v2_routes
from sagewai.admin.state_file import AdminStateFile
from sagewai.connections.protocols import oauth2 as oauth2_module
from sagewai.oauth import pending_auth


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def master_key(monkeypatch) -> str:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", key)
    return key


@pytest.fixture
def state_path(tmp_path: Path, monkeypatch) -> Path:
    sp = tmp_path / "admin-state.json"
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(sp))
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    return sp


@pytest.fixture
def sf(state_path: Path) -> AdminStateFile:
    sf = AdminStateFile(state_path)
    sf.complete_setup(
        org_name="Test Org",
        admin_email="admin@example.com",
        admin_password="hunter22",
    )
    return sf


@pytest.fixture
def token(sf: AdminStateFile) -> str:
    result = sf.validate_login("admin@example.com", "hunter22")
    assert result is not None
    return result["access_token"]


@pytest.fixture
def client(sf: AdminStateFile, token: str, master_key: str) -> TestClient:
    """TestClient with a logged-in cookie."""
    pending_auth.reset_default_store_for_tests()
    oauth2_module._test_inject_context(None)
    app = FastAPI()
    connections_v2_routes.register(app, sf)
    tc = TestClient(app, raise_server_exceptions=True)
    tc.cookies.set("sagewai_auth", token)
    yield tc
    oauth2_module._test_inject_context(None)


# ── /protocols, /backends ───────────────────────────────────────────


def test_get_protocols_lists_9(client: TestClient):
    resp = client.get("/api/v1/admin/connections/protocols")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {p["id"] for p in body}
    assert ids == {"http", "sdk", "mcp", "inference", "oauth2", "coap", "modbus", "opcua", "websocket"}


def test_get_backends_lists_5(client: TestClient):
    resp = client.get("/api/v1/admin/connections/backends")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {b["id"] for b in body}
    assert ids == {"local", "env", "sops", "vault", "doppler"}


# ── /list ────────────────────────────────────────────────────────────


def test_list_empty_returns_empty(client: TestClient):
    resp = client.get("/api/v1/admin/connections/")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_list_filters_by_protocol(client: TestClient):
    # Create an http and an oauth2 conn
    client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "http", "display_name": "H1", "tags": [],
            "protocol_data": {
                "base_url": "https://api.x", "auth": {"kind": "none"},
            },
        },
    )
    client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "oauth2", "display_name": "O1", "tags": [],
            "protocol_data": {
                "provider": "spotify", "client_id": "c", "client_secret": "s",
                "redirect_uri": "http://localhost/cb",
                "requested_scopes": ["user-read-private"],
                "granted_scopes": [], "tokens": None,
            },
        },
    )
    resp = client.get("/api/v1/admin/connections/?protocol=oauth2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["protocol"] == "oauth2"


# ── POST / (create) ──────────────────────────────────────────────────


def test_create_oauth2_connection_returns_masked_record(client: TestClient):
    resp = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "oauth2", "display_name": "Spotify Marketing", "tags": [],
            "protocol_data": {
                "provider": "spotify", "client_id": "cid", "client_secret": "csec",
                "redirect_uri": "http://localhost/cb",
                "requested_scopes": ["user-read-private"],
                "granted_scopes": [], "tokens": None,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["protocol"] == "oauth2"
    # Masked: client_secret is "***" not actual secret
    assert body["protocol_data"]["client_secret"] == "***"


def test_create_invalid_protocol_data_returns_422(client: TestClient):
    resp = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "oauth2", "display_name": "Bad", "tags": [],
            "protocol_data": {
                "provider": "spotify",
                # missing required client_id, client_secret, redirect_uri
                "requested_scopes": ["user-read-private"],
            },
        },
    )
    assert resp.status_code == 422


def test_create_unknown_protocol_returns_400(client: TestClient):
    resp = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "nonexistent", "display_name": "X", "tags": [],
            "protocol_data": {},
        },
    )
    assert resp.status_code in (400, 422)


# ── GET /{id} ────────────────────────────────────────────────────────


def test_get_by_id_masks_secrets(client: TestClient):
    created = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "oauth2", "display_name": "S", "tags": [],
            "protocol_data": {
                "provider": "spotify", "client_id": "cid", "client_secret": "csec",
                "redirect_uri": "http://localhost/cb",
                "requested_scopes": ["user-read-private"],
                "granted_scopes": [], "tokens": None,
            },
        },
    ).json()
    resp = client.get(f"/api/v1/admin/connections/{created['id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["protocol_data"]["client_secret"] == "***"


def test_get_by_id_404_for_missing(client: TestClient):
    resp = client.get("/api/v1/admin/connections/nope")
    assert resp.status_code == 404


# ── PATCH /{id} ──────────────────────────────────────────────────────


def test_patch_changes_display_name(client: TestClient):
    created = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "http", "display_name": "Before", "tags": [],
            "protocol_data": {
                "base_url": "https://api.x", "auth": {"kind": "none"},
            },
        },
    ).json()
    resp = client.patch(
        f"/api/v1/admin/connections/{created['id']}",
        json={"display_name": "After"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "After"


def test_patch_404_for_missing(client: TestClient):
    resp = client.patch(
        "/api/v1/admin/connections/nope",
        json={"display_name": "X"},
    )
    assert resp.status_code == 404


def test_patch_change_protocol_data_re_validates(client: TestClient):
    created = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "oauth2", "display_name": "X", "tags": [],
            "protocol_data": {
                "provider": "spotify", "client_id": "c", "client_secret": "s",
                "redirect_uri": "http://localhost/cb",
                "requested_scopes": ["user-read-private"],
                "granted_scopes": [], "tokens": None,
            },
        },
    ).json()
    resp = client.patch(
        f"/api/v1/admin/connections/{created['id']}",
        json={"protocol_data": {
            "provider": "spotify",
            # missing required fields
            "requested_scopes": ["user-read-private"],
        }},
    )
    assert resp.status_code == 422


# ── DELETE /{id} ─────────────────────────────────────────────────────


def test_delete_returns_204(client: TestClient):
    created = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "http", "display_name": "X", "tags": [],
            "protocol_data": {
                "base_url": "https://api.x", "auth": {"kind": "none"},
            },
        },
    ).json()
    resp = client.delete(f"/api/v1/admin/connections/{created['id']}")
    assert resp.status_code == 204


def test_delete_404_for_missing(client: TestClient):
    resp = client.delete("/api/v1/admin/connections/nope")
    assert resp.status_code == 404


# ── POST /{id}/set-default ───────────────────────────────────────────


def test_set_default_swaps_flag(client: TestClient):
    a = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "http", "display_name": "A", "tags": [],
            "protocol_data": {
                "base_url": "https://api.a", "auth": {"kind": "none"},
            },
        },
    ).json()
    b = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "http", "display_name": "B", "tags": [],
            "protocol_data": {
                "base_url": "https://api.b", "auth": {"kind": "none"},
            },
        },
    ).json()
    # a should be default by virtue of being first
    assert a["is_default"] is True
    assert b["is_default"] is False
    resp = client.post(f"/api/v1/admin/connections/{b['id']}/set-default")
    assert resp.status_code == 200, resp.text
    # Now b is default
    assert resp.json()["is_default"] is True


def test_set_default_404_for_missing(client: TestClient):
    resp = client.post("/api/v1/admin/connections/nope/set-default")
    assert resp.status_code == 404


# ── POST /{id}/test ──────────────────────────────────────────────────


def test_test_route_records_result(client: TestClient):
    """Test an oauth2 connection with no tokens — plugin returns ok=False."""
    created = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "oauth2", "display_name": "S", "tags": [],
            "protocol_data": {
                "provider": "spotify", "client_id": "c", "client_secret": "s",
                "redirect_uri": "http://localhost/cb",
                "requested_scopes": ["user-read-private"],
                "granted_scopes": [], "tokens": None,
            },
        },
    ).json()
    resp = client.post(f"/api/v1/admin/connections/{created['id']}/test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    # Re-fetch — last_test_ok recorded
    refreshed = client.get(f"/api/v1/admin/connections/{created['id']}").json()
    assert refreshed["last_test_ok"] is False


def test_test_route_404_for_missing(client: TestClient):
    resp = client.post("/api/v1/admin/connections/nope/test")
    assert resp.status_code == 404


# ── Project isolation ────────────────────────────────────────────────


def test_project_isolation_in_list(client: TestClient):
    client.post(
        "/api/v1/admin/connections/",
        headers={"X-Project-ID": "alpha"},
        json={
            "protocol": "http", "display_name": "A", "tags": [],
            "protocol_data": {
                "base_url": "https://api.a", "auth": {"kind": "none"},
            },
        },
    )
    client.post(
        "/api/v1/admin/connections/",
        headers={"X-Project-ID": "beta"},
        json={
            "protocol": "http", "display_name": "B", "tags": [],
            "protocol_data": {
                "base_url": "https://api.b", "auth": {"kind": "none"},
            },
        },
    )
    resp_alpha = client.get(
        "/api/v1/admin/connections/", headers={"X-Project-ID": "alpha"}
    )
    body_alpha = resp_alpha.json()
    assert len(body_alpha) == 1
    assert body_alpha[0]["display_name"] == "A"
    resp_beta = client.get(
        "/api/v1/admin/connections/", headers={"X-Project-ID": "beta"}
    )
    body_beta = resp_beta.json()
    assert len(body_beta) == 1
    assert body_beta[0]["display_name"] == "B"


# ── Plugin sub-router mount ─────────────────────────────────────────


def test_oauth2_plugin_routes_mounted_under_oauth2_prefix(client: TestClient):
    """Confirm POST /api/v1/admin/connections/oauth2/{id}/start works."""
    created = client.post(
        "/api/v1/admin/connections/",
        json={
            "protocol": "oauth2", "display_name": "S", "tags": [],
            "protocol_data": {
                "provider": "spotify", "client_id": "c", "client_secret": "s",
                "redirect_uri": "http://localhost/cb",
                "requested_scopes": ["user-read-private"],
                "granted_scopes": [], "tokens": None,
            },
        },
    ).json()
    resp = client.post(
        f"/api/v1/admin/connections/oauth2/{created['id']}/start"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["authorize_url"].startswith("https://accounts.spotify.com/authorize?")
    assert body["state"]


# ── Auth ────────────────────────────────────────────────────────────


def test_unauth_returns_401(sf: AdminStateFile, master_key: str):
    """No cookie → 401."""
    pending_auth.reset_default_store_for_tests()
    app = FastAPI()
    connections_v2_routes.register(app, sf)
    tc = TestClient(app, raise_server_exceptions=True)
    resp = tc.get("/api/v1/admin/connections/protocols")
    assert resp.status_code == 401
