# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for /api/v1/admin/connections/* (formerly inference-providers, Gap #10)."""
from __future__ import annotations

import json
import os

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI


@pytest.fixture
def vault_paths(tmp_path, monkeypatch):
    """Sandbox the on-disk vault to a tmp path and provide a master key."""
    state_file = tmp_path / "admin-state.json"
    state_file.write_text("{}")
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    # Provide a Fernet master key via env so encrypt/decrypt resolves.
    master_key = Fernet.generate_key()
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", master_key.decode("ascii"))

    yield {
        "state_file": state_file,
        "vault_path": tmp_path / "inference-providers.json",
    }


@pytest.fixture
async def client(vault_paths):
    from sagewai.admin import connections_routes

    app = FastAPI()
    connections_routes.register(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test",
    ) as cl:
        yield cl


@pytest.mark.asyncio
async def test_catalog_lists_five_providers(client):
    res = await client.get("/api/v1/admin/connections/catalog")
    assert res.status_code == 200
    body = res.json()
    keys = {p["provider"] for p in body["providers"]}
    assert keys == {"runpod", "modal", "vastai", "colab", "custom"}
    # Custom advertises non-secret keys
    custom = next(p for p in body["providers"] if p["provider"] == "custom")
    assert "CUSTOM_BASE_URL" in custom["env_keys"]


@pytest.mark.asyncio
async def test_list_returns_unconfigured_card_per_provider(client):
    res = await client.get("/api/v1/admin/connections")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 5
    assert all(r["configured"] is False for r in rows)
    assert all(r["secret_keys"] == [] for r in rows)


@pytest.mark.asyncio
async def test_upsert_runpod_then_get(client, vault_paths):
    res = await client.put(
        "/api/v1/admin/connections/runpod",
        json={"secrets": {"RUNPOD_API_KEY": "rp_test_abc123"}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is True
    assert body["secret_keys"] == ["RUNPOD_API_KEY"]
    # The on-disk file holds the encrypted value, not the plaintext
    raw = vault_paths["vault_path"].read_text()
    assert "rp_test_abc123" not in raw
    assert "fernet:" in raw


@pytest.mark.asyncio
async def test_upsert_rejects_unknown_secret_keys(client):
    res = await client.put(
        "/api/v1/admin/connections/runpod",
        json={"secrets": {"NOT_RUNPOD_KEY": "x"}},
    )
    assert res.status_code == 400
    body = res.json()
    assert body["detail"]["unknown_secret_keys"] == ["NOT_RUNPOD_KEY"]


@pytest.mark.asyncio
async def test_project_scoping_isolates_credentials(client, vault_paths):
    # Save a credential under project A
    await client.put(
        "/api/v1/admin/connections/runpod",
        json={"secrets": {"RUNPOD_API_KEY": "key-from-a"}},
        headers={"X-Project-ID": "project-a"},
    )
    # Project B should see an unconfigured card
    res_b = await client.get(
        "/api/v1/admin/connections/runpod",
        headers={"X-Project-ID": "project-b"},
    )
    assert res_b.status_code == 200
    assert res_b.json()["configured"] is False

    # Project A still sees its own
    res_a = await client.get(
        "/api/v1/admin/connections/runpod",
        headers={"X-Project-ID": "project-a"},
    )
    assert res_a.json()["configured"] is True


@pytest.mark.asyncio
async def test_delete_removes_credentials(client):
    await client.put(
        "/api/v1/admin/connections/runpod",
        json={"secrets": {"RUNPOD_API_KEY": "x"}},
    )
    res = await client.delete("/api/v1/admin/connections/runpod")
    assert res.status_code == 204
    after = await client.get("/api/v1/admin/connections/runpod")
    assert after.json()["configured"] is False


@pytest.mark.asyncio
async def test_delete_404_when_not_configured(client):
    res = await client.delete("/api/v1/admin/connections/runpod")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_test_connection_no_credentials_returns_ok_false(client):
    res = await client.post(
        "/api/v1/admin/connections/runpod/test"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "No credentials" in body["detail"]


@pytest.mark.asyncio
async def test_test_connection_colab_validates_oauth_json(client):
    valid = json.dumps({"installed": {"client_id": "abc.apps.googleusercontent.com"}})
    await client.put(
        "/api/v1/admin/connections/colab",
        json={"secrets": {"GOOGLE_DRIVE_OAUTH_JSON": valid}},
    )
    res = await client.post(
        "/api/v1/admin/connections/colab/test"
    )
    body = res.json()
    assert body["ok"] is True, body
    assert "client_id" in body["detail"]


@pytest.mark.asyncio
async def test_test_connection_colab_rejects_bad_json(client):
    await client.put(
        "/api/v1/admin/connections/colab",
        json={"secrets": {"GOOGLE_DRIVE_OAUTH_JSON": "{not json"}},
    )
    res = await client.post(
        "/api/v1/admin/connections/colab/test"
    )
    body = res.json()
    assert body["ok"] is False
    assert "did not parse" in body["detail"]


@pytest.mark.asyncio
async def test_test_connection_custom_requires_base_url(client):
    await client.put(
        "/api/v1/admin/connections/custom",
        json={
            "secrets": {"CUSTOM_AUTH_VALUE": "abc"},
            "env": {},
            "auth_shape": "bearer",
        },
    )
    res = await client.post(
        "/api/v1/admin/connections/custom/test"
    )
    body = res.json()
    assert body["ok"] is False
    assert "CUSTOM_BASE_URL" in body["detail"]


@pytest.mark.asyncio
async def test_unknown_provider_404(client):
    res = await client.get("/api/v1/admin/connections/spheron")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_upsert_without_master_key_returns_503(
    monkeypatch, vault_paths,
):
    """If the Sealed master key is missing, save returns a clean 503 with a
    pointer to ``sagewai admin sealed init``."""
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    # Also disable keyring + the file path so resolution definitely fails.
    monkeypatch.setattr(
        "sagewai.sealed.master_key.keyring", None,
    )
    monkeypatch.setattr(
        "sagewai.sealed.master_key.DEFAULT_KEY_PATH",
        vault_paths["vault_path"].parent / "nonexistent.key",
    )

    from sagewai.admin import connections_routes

    app = FastAPI()
    connections_routes.register(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test",
    ) as cl:
        res = await cl.put(
            "/api/v1/admin/connections/runpod",
            json={"secrets": {"RUNPOD_API_KEY": "x"}},
        )
    assert res.status_code == 503
    assert "sagewai admin sealed init" in res.json()["detail"]


@pytest.mark.asyncio
async def test_test_connection_records_outcome_on_card(client):
    # Save Colab with valid OAuth JSON and run the (in-process) test.
    valid = json.dumps({"installed": {"client_id": "abc.apps.googleusercontent.com"}})
    await client.put(
        "/api/v1/admin/connections/colab",
        json={"secrets": {"GOOGLE_DRIVE_OAUTH_JSON": valid}},
    )
    await client.post("/api/v1/admin/connections/colab/test")
    res = await client.get("/api/v1/admin/connections/colab")
    body = res.json()
    assert body["last_test_ok"] is True
    assert body["last_tested_at"] is not None
