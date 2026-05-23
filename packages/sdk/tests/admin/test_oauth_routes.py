# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for /api/v1/admin/connections/oauth/* routes.

Covers the OAuth admin surface: provider listing, client CRUD,
authorize-flow start, vendor callback, refresh, revoke, set-default,
and project isolation. Vendor HTTP endpoints (token exchange, refresh,
revoke) are mocked via ``respx``.
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin import oauth_routes
from sagewai.admin.state_file import AdminStateFile
from sagewai.oauth import pending_auth, vault
from sagewai.sealed.crypto import Crypto


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def master_key(monkeypatch) -> str:
    """Provide a stable Fernet-format master key for Sealed.Crypto."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", key)
    return key


@pytest.fixture()
def state_path(tmp_path: Path, monkeypatch) -> Path:
    """Point both the admin-state file and the oauth vault at tmp_path."""
    sp = tmp_path / "admin-state.json"
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(sp))
    return sp


@pytest.fixture()
def store_path(state_path: Path) -> Path:
    """Vault file path derived by the module from SAGEWAI_ADMIN_STATE_FILE."""
    return state_path.parent / "inference-providers.json"


@pytest.fixture()
def sf(state_path: Path) -> AdminStateFile:
    """Authenticated AdminStateFile — setup completed, one admin token issued."""
    sf = AdminStateFile(state_path)
    sf.complete_setup(
        org_name="Test Org",
        admin_email="admin@example.com",
        admin_password="hunter22",
    )
    return sf


@pytest.fixture()
def token(sf: AdminStateFile) -> str:
    result = sf.validate_login("admin@example.com", "hunter22")
    assert result is not None, "Login failed in fixture"
    return result["access_token"]


@pytest.fixture()
def client(sf: AdminStateFile, token: str, master_key: str) -> TestClient:
    """TestClient with a logged-in cookie + project header default."""
    pending_auth.reset_default_store_for_tests()
    app = FastAPI()
    oauth_routes.register(app, sf)
    tc = TestClient(app, raise_server_exceptions=True)
    tc.cookies.set("sagewai_auth", token)
    return tc


# ── Helpers ────────────────────────────────────────────────────────


def _create_spotify_client(client: TestClient, *, project: str = "acme") -> dict:
    """POST /oauth/ to create a pending Spotify oauth_client. Return the JSON body."""
    resp = client.post(
        "/api/v1/admin/connections/oauth/",
        headers={"X-Project-ID": project},
        json={
            "provider": "spotify",
            "display_name": "Spotify (Marketing)",
            "client_id": "spot-client-id",
            "client_secret": "spot-client-secret",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seed_authorized_spotify(
    store_path: Path,
    *,
    project_id: str | None = "acme",
) -> str:
    """Directly write an authorized Spotify oauth_client; return its id.

    Bypasses the admin route so the refresh/revoke tests don't depend on
    the callback path having been wired correctly first.
    """
    crypto = Crypto(_master_key_bytes())
    created = vault.create_client(
        store_path,
        crypto,
        provider="spotify",
        project_id=project_id,
        display_name="Spotify (Marketing)",
        client_id="spot-client-id",
        client_secret="spot-client-secret",
        redirect_uri="http://testserver/api/v1/admin/connections/oauth/callback",
        requested_scopes=["user-read-private"],
    )
    client_id = created["id"]
    vault.update_tokens(
        store_path,
        crypto,
        client_id,
        tokens={
            "access_token": "spot-access-OLD",
            "refresh_token": "spot-refresh-OLD",
            "token_type": "Bearer",
            "expires_at": "2030-01-01T00:00:00Z",
        },
        granted_scopes=["user-read-private"],
        status="authorized",
    )
    return client_id


def _seed_authorized_google(
    store_path: Path,
    *,
    project_id: str | None = "acme",
) -> str:
    """Same idea, but for Google so we exercise the revoke_url branch."""
    crypto = Crypto(_master_key_bytes())
    created = vault.create_client(
        store_path,
        crypto,
        provider="google",
        project_id=project_id,
        display_name="Google (Workspace)",
        client_id="google-client-id",
        client_secret="google-client-secret",
        redirect_uri="http://testserver/api/v1/admin/connections/oauth/callback",
        requested_scopes=["openid", "email"],
    )
    client_id = created["id"]
    vault.update_tokens(
        store_path,
        crypto,
        client_id,
        tokens={
            "access_token": "google-access-OLD",
            "refresh_token": "google-refresh-OLD",
            "token_type": "Bearer",
            "expires_at": "2030-01-01T00:00:00Z",
        },
        granted_scopes=["openid", "email"],
        status="authorized",
    )
    return client_id


def _master_key_bytes() -> bytes:
    import os
    return os.environ["SAGEWAI_MASTER_KEY"].encode("ascii")


# ── Tests ───────────────────────────────────────────────────────────


def test_list_providers_returns_registry(client: TestClient) -> None:
    resp = client.get("/api/v1/admin/connections/oauth/providers")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {p["id"] for p in body}
    assert {"spotify", "google"} <= ids
    spotify = next(p for p in body if p["id"] == "spotify")
    assert spotify["display_name"] == "Spotify"
    assert "user-read-private" in spotify["default_scopes"]
    assert spotify["docs_url"].startswith("https://")


def test_unauthenticated_returns_401(sf: AdminStateFile, master_key: str) -> None:
    app = FastAPI()
    oauth_routes.register(app, sf)
    tc = TestClient(app, raise_server_exceptions=True)
    resp = tc.get(
        "/api/v1/admin/connections/oauth/providers",
        headers={"X-Project-ID": "acme"},
    )
    assert resp.status_code == 401


def test_post_creates_pending_record_and_returns_authorize_url(
    client: TestClient, store_path: Path,
) -> None:
    body = _create_spotify_client(client)
    # Returned envelope shape
    assert "record" in body
    assert "authorize_url" in body
    assert "state" in body
    record = body["record"]
    assert record["status"] == "pending"
    assert record["provider"] == "spotify"
    # Masking
    assert "client_secret" not in record
    # Authorize URL points at the right endpoint
    assert body["authorize_url"].startswith("https://accounts.spotify.com/authorize?")
    assert body["state"]
    # Vault row exists
    assert store_path.exists()


def test_post_start_returns_well_formed_authorize_url(client: TestClient) -> None:
    created = _create_spotify_client(client)
    rid = created["record"]["id"]
    resp = client.post(
        f"/api/v1/admin/connections/oauth/{rid}/start",
        headers={"X-Project-ID": "acme"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    url = body["authorize_url"]
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["spot-client-id"]
    assert (
        qs["redirect_uri"][0]
        == "http://testserver/api/v1/admin/connections/oauth/callback"
    )
    # scope must be space-joined per Spotify's provider config
    assert "user-read-private" in qs["scope"][0]
    assert qs["state"] == [body["state"]]
    assert qs["code_challenge"]
    assert qs["code_challenge_method"] == ["S256"]


@respx.mock
def test_callback_happy_path_completes_flow(
    client: TestClient, store_path: Path,
) -> None:
    created = _create_spotify_client(client)
    rid = created["record"]["id"]

    # Start the dance so a pending-auth entry exists.
    start_resp = client.post(
        f"/api/v1/admin/connections/oauth/{rid}/start",
        headers={"X-Project-ID": "acme"},
    )
    state = start_resp.json()["state"]

    respx.post("https://accounts.spotify.com/api/token").respond(
        200,
        json={
            "access_token": "spot-access-NEW",
            "refresh_token": "spot-refresh-NEW",
            "expires_in": 3600,
            "scope": "user-read-private playlist-read-private",
            "token_type": "Bearer",
        },
    )

    cb = client.get(
        f"/api/v1/admin/connections/oauth/callback?code=AUTHCODE&state={state}"
    )
    assert cb.status_code == 200, cb.text
    assert "text/html" in cb.headers["content-type"].lower()

    # Vault row updated.
    record = vault.get_client(store_path, rid)
    assert record is not None
    assert record["status"] == "authorized"
    assert record["granted_scopes"]  # populated from response
    # tokens is present but masked
    assert record["tokens"] is not None
    assert "access_token" not in record["tokens"]


def test_callback_invalid_state_returns_400(client: TestClient) -> None:
    cb = client.get(
        "/api/v1/admin/connections/oauth/callback?code=X&state=garbage-nonce"
    )
    assert cb.status_code in (400, 410)
    assert "text/html" in cb.headers["content-type"].lower()


def test_callback_with_error_param_returns_html(
    client: TestClient, store_path: Path,
) -> None:
    created = _create_spotify_client(client)
    rid = created["record"]["id"]
    start_resp = client.post(
        f"/api/v1/admin/connections/oauth/{rid}/start",
        headers={"X-Project-ID": "acme"},
    )
    state = start_resp.json()["state"]

    cb = client.get(
        f"/api/v1/admin/connections/oauth/callback"
        f"?error=access_denied&state={state}"
    )
    # Informational, not a 5xx
    assert cb.status_code == 200, cb.text
    assert "text/html" in cb.headers["content-type"].lower()
    # Record stays pending — operator can retry.
    record = vault.get_client(store_path, rid)
    assert record is not None
    assert record["status"] == "pending"


def test_callback_expired_pending_auth_returns_4xx(
    client: TestClient, store_path: Path, monkeypatch,
) -> None:
    created = _create_spotify_client(client)
    rid = created["record"]["id"]
    start_resp = client.post(
        f"/api/v1/admin/connections/oauth/{rid}/start",
        headers={"X-Project-ID": "acme"},
    )
    state = start_resp.json()["state"]

    # Force the pending-auth entry to be considered expired.
    store = pending_auth.get_default_store()
    with store._lock:  # type: ignore[attr-defined]
        for k, (_ts, entry) in list(store._entries.items()):  # type: ignore[attr-defined]
            store._entries[k] = (0.0, entry)  # type: ignore[attr-defined]

    cb = client.get(
        f"/api/v1/admin/connections/oauth/callback?code=X&state={state}"
    )
    assert cb.status_code in (400, 410)


@respx.mock
def test_refresh_happy_path_updates_expires_at(
    client: TestClient, store_path: Path,
) -> None:
    rid = _seed_authorized_spotify(store_path)
    respx.post("https://accounts.spotify.com/api/token").respond(
        200,
        json={
            "access_token": "spot-access-REFRESHED",
            "refresh_token": "spot-refresh-OLD",
            "expires_in": 3600,
            "scope": "user-read-private",
            "token_type": "Bearer",
        },
    )
    resp = client.post(
        f"/api/v1/admin/connections/oauth/{rid}/refresh",
        headers={"X-Project-ID": "acme"},
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()
    assert record["status"] == "authorized"
    assert "expires_at" in record["tokens"]


@respx.mock
def test_refresh_vendor_failure_marks_expired(
    client: TestClient, store_path: Path,
) -> None:
    rid = _seed_authorized_spotify(store_path)
    respx.post("https://accounts.spotify.com/api/token").respond(
        400, json={"error": "invalid_grant"},
    )
    resp = client.post(
        f"/api/v1/admin/connections/oauth/{rid}/refresh",
        headers={"X-Project-ID": "acme"},
    )
    assert 400 <= resp.status_code < 500
    record = vault.get_client(store_path, rid)
    assert record is not None
    assert record["status"] == "expired"


@respx.mock
def test_revoke_clears_tokens_and_sets_status(
    client: TestClient, store_path: Path,
) -> None:
    rid = _seed_authorized_google(store_path)
    revoke_route = respx.post("https://oauth2.googleapis.com/revoke").respond(200, json={})
    resp = client.post(
        f"/api/v1/admin/connections/oauth/{rid}/revoke",
        headers={"X-Project-ID": "acme"},
    )
    assert resp.status_code == 200, resp.text
    assert revoke_route.called
    record = vault.get_client(store_path, rid)
    assert record is not None
    assert record["status"] == "revoked"
    assert record["tokens"] is None


def test_project_isolation_in_list(client: TestClient) -> None:
    # Two pending records, one per project.
    client.post(
        "/api/v1/admin/connections/oauth/",
        headers={"X-Project-ID": "acme"},
        json={
            "provider": "spotify",
            "display_name": "Acme Spotify",
            "client_id": "acme-id",
            "client_secret": "acme-secret",
        },
    )
    client.post(
        "/api/v1/admin/connections/oauth/",
        headers={"X-Project-ID": "beta"},
        json={
            "provider": "spotify",
            "display_name": "Beta Spotify",
            "client_id": "beta-id",
            "client_secret": "beta-secret",
        },
    )

    acme_list = client.get(
        "/api/v1/admin/connections/oauth/",
        headers={"X-Project-ID": "acme"},
    ).json()
    beta_list = client.get(
        "/api/v1/admin/connections/oauth/",
        headers={"X-Project-ID": "beta"},
    ).json()
    assert len(acme_list) == 1
    assert acme_list[0]["display_name"] == "Acme Spotify"
    assert len(beta_list) == 1
    assert beta_list[0]["display_name"] == "Beta Spotify"


def test_set_default_unsets_prior(client: TestClient) -> None:
    first = _create_spotify_client(client)
    second_resp = client.post(
        "/api/v1/admin/connections/oauth/",
        headers={"X-Project-ID": "acme"},
        json={
            "provider": "spotify",
            "display_name": "Spotify (Backup)",
            "client_id": "spot-id-2",
            "client_secret": "spot-secret-2",
        },
    )
    second = second_resp.json()
    assert first["record"]["is_default"] is True
    assert second["record"]["is_default"] is False

    resp = client.post(
        f"/api/v1/admin/connections/oauth/{second['record']['id']}/set-default",
        headers={"X-Project-ID": "acme"},
    )
    assert resp.status_code == 200, resp.text
    after = client.get(
        "/api/v1/admin/connections/oauth/",
        headers={"X-Project-ID": "acme"},
    ).json()
    by_id = {r["id"]: r for r in after}
    assert by_id[second["record"]["id"]]["is_default"] is True
    assert by_id[first["record"]["id"]]["is_default"] is False


def test_secrets_never_leak_in_get_response(client: TestClient) -> None:
    created = _create_spotify_client(client)
    rid = created["record"]["id"]
    resp = client.get(
        f"/api/v1/admin/connections/oauth/{rid}",
        headers={"X-Project-ID": "acme"},
    )
    assert resp.status_code == 200
    body_text = resp.text
    assert "spot-client-secret" not in body_text
    body = resp.json()
    assert "client_secret" not in body


def test_unknown_provider_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/admin/connections/oauth/",
        headers={"X-Project-ID": "acme"},
        json={
            "provider": "not-a-real-provider",
            "display_name": "Bogus",
            "client_id": "x",
            "client_secret": "y",
        },
    )
    assert resp.status_code == 404


def test_delete_client_removes_record(
    client: TestClient, store_path: Path,
) -> None:
    created = _create_spotify_client(client)
    rid = created["record"]["id"]
    resp = client.delete(
        f"/api/v1/admin/connections/oauth/{rid}",
        headers={"X-Project-ID": "acme"},
    )
    assert resp.status_code == 204
    assert vault.get_client(store_path, rid) is None
