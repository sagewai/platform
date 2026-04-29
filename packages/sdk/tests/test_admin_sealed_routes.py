# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for /api/v1/admin/sealed/* routes."""

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
from sagewai.sealed.crypto import Crypto
from sagewai.sealed.refs import _BACKENDS


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    crypto = Crypto(Fernet.generate_key())
    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=crypto,
        audit_writer=None,
    )
    monkeypatch.setitem(_BACKENDS, "builtin", backend)

    from sagewai.admin import sealed_routes

    app = FastAPI()
    sealed_routes.register(app, store=None)
    return TestClient(app)


def test_list_empty(admin_client):
    res = admin_client.get("/api/v1/admin/sealed/profiles")
    assert res.status_code == 200
    assert res.json() == []


def test_create_then_list(admin_client):
    res = admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "acme", "name": "Acme", "secrets": {"K": "v"}, "env": {"E": "x"}},
    )
    assert res.status_code == 201, res.text
    assert res.json()["id"] == "acme"

    res = admin_client.get("/api/v1/admin/sealed/profiles")
    assert len(res.json()) == 1


def test_get_metadata_excludes_secrets(admin_client):
    admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "acme", "name": "Acme", "secrets": {"K": "v"}},
    )
    res = admin_client.get("/api/v1/admin/sealed/profiles/acme")
    assert res.status_code == 200
    body = res.json()
    assert body["secret_keys"] == ["K"]
    assert "secrets" not in body


def test_get_full_includes_secrets(admin_client):
    admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "acme", "name": "Acme", "secrets": {"K": "v"}},
    )
    res = admin_client.get("/api/v1/admin/sealed/profiles/acme/full")
    assert res.status_code == 200
    assert res.json()["secrets"] == {"K": "v"}


def test_get_unknown_returns_404(admin_client):
    res = admin_client.get("/api/v1/admin/sealed/profiles/ghost")
    assert res.status_code == 404


def test_update_replaces(admin_client):
    admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "acme", "name": "Acme"},
    )
    res = admin_client.put(
        "/api/v1/admin/sealed/profiles/acme",
        json={"name": "Acme Updated", "secrets": {"NEW": "v"}},
    )
    assert res.status_code == 200
    assert res.json()["name"] == "Acme Updated"
    assert res.json()["secrets"] == {"NEW": "v"}


def test_delete_removes(admin_client):
    admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "acme", "name": "Acme"},
    )
    res = admin_client.delete("/api/v1/admin/sealed/profiles/acme")
    assert res.status_code == 204
    res = admin_client.get("/api/v1/admin/sealed/profiles/acme")
    assert res.status_code == 404


@pytest.fixture(autouse=True)
def _reset_reveal_history():
    from sagewai.admin import sealed_routes

    sealed_routes._REVEAL_HISTORY.clear()
    yield


def test_reveal_returns_value(admin_client):
    admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "acme", "name": "Acme", "secrets": {"K": "secret-v"}},
    )
    res = admin_client.post("/api/v1/admin/sealed/profiles/acme/reveal/K")
    assert res.status_code == 200
    assert res.json() == {"value": "secret-v"}


def test_reveal_unknown_secret_returns_404(admin_client):
    admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "acme", "name": "Acme"},
    )
    res = admin_client.post("/api/v1/admin/sealed/profiles/acme/reveal/MISSING")
    assert res.status_code == 404


@pytest.fixture(autouse=True)
def _isolated_admin_state(tmp_path, monkeypatch):
    state_path = tmp_path / "admin-state.json"
    state_path.write_text("{}")
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_path))
    yield


def test_status_endpoint(admin_client):
    res = admin_client.get("/api/v1/admin/sealed/status")
    assert res.status_code == 200
    body = res.json()
    assert "master_key_configured" in body
    assert "backends_registered" in body
    assert "builtin" in body["backends_registered"]


def test_preview_endpoint_resolves(admin_client, monkeypatch, tmp_path):
    # Prime admin-state so the preview cascade has something to read
    state_path = tmp_path / "admin-state.json"
    state_path.write_text("{}")
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_path))

    admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "sys", "name": "S", "env": {"E": "v"}},
    )
    admin_client.put(
        "/api/v1/admin/sealed/system",
        json={"profile_ref": "sys", "overrides": {"X": "1"}},
    )
    res = admin_client.get("/api/v1/admin/sealed/preview?project=acme")
    assert res.status_code == 200
    body = res.json()
    assert body["env"]["E"] == "v"
    assert body["env"]["X"] == "1"


def test_create_profile_with_acl_roundtrips(admin_client):
    """Verify POST /profiles with acl returns acl in response (Sealed-iii.D)."""
    acl = {"claude-code": ["K1"], "codex": ["K2"]}
    res = admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "test-acl", "name": "Test ACL", "acl": acl},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["acl"] == acl, f"Expected acl in response, got {body.get('acl')}"


def test_get_profile_metadata_returns_acl(admin_client):
    """Verify GET /profiles/{id} returns acl field (Sealed-iii.D)."""
    acl = {"shell": ["S1", "S2"], "audit": ["A1"]}
    admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "test-acl-get", "name": "Test ACL Get", "acl": acl},
    )
    res = admin_client.get("/api/v1/admin/sealed/profiles/test-acl-get")
    assert res.status_code == 200
    body = res.json()
    assert body["acl"] == acl, f"Expected acl in metadata, got {body.get('acl')}"


def test_get_profile_full_returns_acl(admin_client):
    """Verify GET /profiles/{id}/full includes acl (Sealed-iii.D)."""
    acl = {"test": ["T1"]}
    admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "test-acl-full", "name": "Test ACL Full", "acl": acl},
    )
    res = admin_client.get("/api/v1/admin/sealed/profiles/test-acl-full/full")
    assert res.status_code == 200
    body = res.json()
    assert body["acl"] == acl, f"Expected acl in full profile, got {body.get('acl')}"


def test_update_profile_with_acl_roundtrips(admin_client):
    """Verify PUT /profiles/{id} with acl returns updated acl (Sealed-iii.D)."""
    # Create with initial ACL
    admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "test-acl-update", "name": "Test ACL Update", "acl": {"v1": ["A"]}},
    )
    # Update with new ACL
    new_acl = {"v2": ["B", "C"], "admin": ["ADM"]}
    res = admin_client.put(
        "/api/v1/admin/sealed/profiles/test-acl-update",
        json={"name": "Test ACL Update", "acl": new_acl},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["acl"] == new_acl, f"Expected updated acl, got {body.get('acl')}"


def test_update_profile_with_empty_acl_roundtrips(admin_client):
    """Verify PUT /profiles/{id} with empty acl list preserves it (not coerced to missing)."""
    # Create with initial ACL
    admin_client.post(
        "/api/v1/admin/sealed/profiles",
        json={"id": "test-acl-empty", "name": "Test ACL Empty", "acl": {"v1": ["A"]}},
    )
    # Update with empty list for one key
    new_acl = {"v1": [], "v2": ["X"]}
    res = admin_client.put(
        "/api/v1/admin/sealed/profiles/test-acl-empty",
        json={"name": "Test ACL Empty", "acl": new_acl},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["acl"] == new_acl, f"Expected acl with empty list preserved, got {body.get('acl')}"
    # Verify it persists on GET
    res = admin_client.get("/api/v1/admin/sealed/profiles/test-acl-empty/full")
    assert res.json()["acl"] == new_acl
