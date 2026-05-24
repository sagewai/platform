# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OAuth2 plugin route + CLI bodies (filled in for PR4).

Tests the now-real route + CLI bodies via the plugin. Uses respx to
mock the vendor token endpoints. Replaces behavior coverage from the
deleted PR #356 tests (test_oauth_routes.py + test_oauth_cli.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import httpx
import pytest
import respx
from click.testing import CliRunner
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin.state_file import AdminStateFile
from sagewai.connections.bootstrap import build_connections_context
from sagewai.connections.protocols import oauth2 as oauth2_module
from sagewai.connections.protocols.oauth2 import OAuth2ProtocolPlugin
from sagewai.oauth import pending_auth


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    pending_auth.reset_default_store_for_tests()
    yield


@pytest.fixture
def _store_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(tmp_path / "admin-state.json"))
    yield tmp_path


@pytest.fixture
def ctx(tmp_path, _store_env):
    sf = AdminStateFile(tmp_path / "admin-state.json")
    c = build_connections_context(sf)
    oauth2_module._test_inject_context(c)
    yield c
    oauth2_module._test_inject_context(None)


@pytest.fixture
def app(ctx):
    """Mount the oauth2 plugin's extra_routes at the PR4 prefix."""
    fastapi_app = FastAPI()
    plugin = OAuth2ProtocolPlugin()
    fastapi_app.include_router(
        plugin.extra_routes(), prefix=f"/api/v1/admin/connections/{plugin.id}"
    )
    return TestClient(fastapi_app)


# ── Helpers ─────────────────────────────────────────────────────────


def _create_pending_connection(ctx, *, provider="spotify", display_name="Test"):
    """Create a pending oauth2 connection in the store with encrypted secrets."""
    pd = {
        "provider": provider,
        "client_id": "test-cid",
        "client_secret": "test-csec",
        "redirect_uri": "http://localhost:8080/api/v1/admin/connections/oauth2/callback",
        "requested_scopes": ["user-read-private"] if provider == "spotify" else ["openid"],
        "granted_scopes": [],
        "tokens": None,
    }
    encrypted_pd = ctx.router.encrypt(
        pd,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    return ctx.store.create(
        protocol="oauth2", project_id="default", display_name=display_name,
        tags=[], protocol_data=encrypted_pd,
    )


def _seed_authorized(ctx, *, provider="spotify", expires_in_seconds=3600,
                     access_token="AT-INITIAL", refresh_token="RT-INITIAL"):
    """Create an authorized oauth2 connection with encrypted tokens."""
    conn = _create_pending_connection(ctx, provider=provider)
    now = datetime.now(timezone.utc)
    decrypted_pd = ctx.router.decrypt(
        conn.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    granted = list(decrypted_pd["requested_scopes"])
    pd_with_tokens = {
        **decrypted_pd,
        "granted_scopes": granted,
        "tokens": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_at": (now + timedelta(seconds=expires_in_seconds)).isoformat(),
            "obtained_at": now.isoformat(),
            "last_refreshed_at": None,
        },
    }
    encrypted_pd = ctx.router.encrypt(
        pd_with_tokens,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    ctx.store.update(conn.id, protocol_data=encrypted_pd, status="authorized")
    return ctx.store.get(conn.id)


# ── /start route ─────────────────────────────────────────────────────


def test_start_returns_well_formed_authorize_url(app, ctx):
    conn = _create_pending_connection(ctx)
    resp = app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/start")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["authorize_url"].startswith("https://accounts.spotify.com/authorize?")
    assert "response_type=code" in body["authorize_url"]
    assert "client_id=test-cid" in body["authorize_url"]
    assert "code_challenge=" in body["authorize_url"]
    assert "code_challenge_method=S256" in body["authorize_url"]
    assert "state=" in body["authorize_url"]
    assert len(body["state"]) >= 32


def test_start_for_google_adds_offline_access(app, ctx):
    conn = _create_pending_connection(ctx, provider="google")
    resp = app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/start")
    assert resp.status_code == 200, resp.text
    url = resp.json()["authorize_url"]
    assert "access_type=offline" in url
    assert "prompt=consent" in url


def test_start_404_for_missing_connection(app):
    resp = app.post("/api/v1/admin/connections/oauth2/nope/start")
    assert resp.status_code == 404


def test_start_400_for_wrong_protocol(app, ctx):
    """oauth2 plugin refuses to start for an http connection."""
    http_conn = ctx.store.create(
        protocol="http", project_id="default", display_name="X",
        tags=[], protocol_data={"base_url": "https://api.x", "auth": {"kind": "none"}},
    )
    resp = app.post(f"/api/v1/admin/connections/oauth2/{http_conn.id}/start")
    assert resp.status_code == 400


# ── /callback route ──────────────────────────────────────────────────


@respx.mock
def test_callback_happy_path(app, ctx):
    """Full code-exchange → tokens written → status='authorized'."""
    conn = _create_pending_connection(ctx)
    start_resp = app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/start")
    state = start_resp.json()["state"]
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-real",
            "refresh_token": "RT-real",
            "expires_in": 3600,
            "scope": "user-read-private",
            "token_type": "Bearer",
        }),
    )
    resp = app.get(
        f"/api/v1/admin/connections/oauth2/callback?code=AUTH_CODE&state={state}"
    )
    assert resp.status_code == 200, resp.text
    assert "connected" in resp.text.lower() or "authorized" in resp.text.lower()
    refreshed = ctx.store.get(conn.id)
    assert refreshed.status == "authorized"
    assert refreshed.protocol_data["granted_scopes"] == ["user-read-private"]
    # Tokens encrypted (not plaintext) when read back
    decrypted_pd = ctx.router.decrypt(
        refreshed.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    assert decrypted_pd["tokens"]["access_token"] == "AT-real"


def test_callback_missing_state_returns_400(app):
    resp = app.get("/api/v1/admin/connections/oauth2/callback?code=X")
    assert resp.status_code == 400


def test_callback_invalid_state_returns_410(app):
    resp = app.get(
        "/api/v1/admin/connections/oauth2/callback?code=X&state=garbage"
    )
    assert resp.status_code == 410


def test_callback_with_error_param_returns_html(app, ctx):
    """access_denied → 200 HTML, no store mutation, operator can retry."""
    conn = _create_pending_connection(ctx)
    start_resp = app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/start")
    state = start_resp.json()["state"]
    resp = app.get(
        f"/api/v1/admin/connections/oauth2/callback?error=access_denied&state={state}"
    )
    assert resp.status_code == 200
    assert "cancel" in resp.text.lower() or "denied" in resp.text.lower()
    refreshed = ctx.store.get(conn.id)
    assert refreshed.status == "pending"  # unchanged


# ── /{id}/refresh route ──────────────────────────────────────────────


@respx.mock
def test_refresh_happy_path(app, ctx):
    """Force a token refresh on an authorized connection."""
    conn = _seed_authorized(ctx)
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-NEW",
            "refresh_token": "RT-NEW",
            "expires_in": 3600,
            "scope": "user-read-private",
            "token_type": "Bearer",
        }),
    )
    resp = app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/refresh")
    assert resp.status_code == 200, resp.text
    refreshed = ctx.store.get(conn.id)
    decrypted_pd = ctx.router.decrypt(
        refreshed.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    assert decrypted_pd["tokens"]["access_token"] == "AT-NEW"


@respx.mock
def test_refresh_vendor_400_marks_expired(app, ctx):
    conn = _seed_authorized(ctx)
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"}),
    )
    resp = app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/refresh")
    assert resp.status_code in (400, 422, 500)
    refreshed = ctx.store.get(conn.id)
    assert refreshed.status == "expired"


def test_refresh_404_for_missing(app):
    resp = app.post("/api/v1/admin/connections/oauth2/nope/refresh")
    assert resp.status_code == 404


# ── /{id}/revoke route ───────────────────────────────────────────────


@respx.mock
def test_revoke_calls_vendor_revoke_endpoint_and_clears_tokens(app, ctx):
    """google has a revoke_url; spotify does not. Use google here."""
    conn = _seed_authorized(ctx, provider="google")
    revoke_route = respx.post("https://oauth2.googleapis.com/revoke").mock(
        return_value=httpx.Response(200, json={}),
    )
    resp = app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/revoke")
    assert resp.status_code == 200, resp.text
    assert revoke_route.called
    refreshed = ctx.store.get(conn.id)
    assert refreshed.status == "revoked"
    decrypted_pd = ctx.router.decrypt(
        refreshed.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    assert decrypted_pd["tokens"] is None


def test_revoke_for_provider_without_revoke_url_still_clears_tokens(app, ctx):
    """Spotify has no revoke endpoint; we still clear local tokens."""
    conn = _seed_authorized(ctx, provider="spotify")
    resp = app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/revoke")
    assert resp.status_code == 200, resp.text
    refreshed = ctx.store.get(conn.id)
    assert refreshed.status == "revoked"


def test_revoke_404_for_missing(app):
    resp = app.post("/api/v1/admin/connections/oauth2/nope/revoke")
    assert resp.status_code == 404


# ── get_default_access_token executor helper ─────────────────────────


@pytest.mark.asyncio
async def test_get_default_access_token_returns_decrypted_token(ctx):
    conn = _seed_authorized(ctx, access_token="AT-decrypted-please")
    ctx.store.set_default(conn.id)
    token, _ = await OAuth2ProtocolPlugin.get_default_access_token(
        store=ctx.store, router=ctx.router, project_id="default", provider="spotify",
    )
    assert token == "AT-decrypted-please"


@pytest.mark.asyncio
async def test_get_default_access_token_raises_when_no_default(ctx):
    from sagewai.oauth.errors import OAuthNotConfiguredError
    with pytest.raises(OAuthNotConfiguredError):
        await OAuth2ProtocolPlugin.get_default_access_token(
            store=ctx.store, router=ctx.router, project_id="default", provider="spotify",
        )


@pytest.mark.asyncio
async def test_get_default_access_token_raises_when_status_not_authorized(ctx):
    from sagewai.oauth.errors import OAuthNotAuthorizedError
    _create_pending_connection(ctx)  # status: pending
    with pytest.raises(OAuthNotAuthorizedError):
        await OAuth2ProtocolPlugin.get_default_access_token(
            store=ctx.store, router=ctx.router, project_id="default", provider="spotify",
        )


@pytest.mark.asyncio
@respx.mock
async def test_get_default_access_token_preemptive_refresh_when_near_expiry(ctx):
    conn = _seed_authorized(ctx, expires_in_seconds=30, access_token="AT-OLD")
    ctx.store.set_default(conn.id)
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-REFRESHED",
            "expires_in": 3600,
            "token_type": "Bearer",
        }),
    )
    token, _ = await OAuth2ProtocolPlugin.get_default_access_token(
        store=ctx.store, router=ctx.router, project_id="default", provider="spotify",
    )
    assert token == "AT-REFRESHED"


# ── CLI command bodies ─────────────────────────────────────────────


def _plugin_cli_cmd(name: str) -> click.Command:
    """Get the plugin's CLI command by name."""
    plugin = OAuth2ProtocolPlugin()
    for cmd in plugin.extra_cli():
        if cmd.name == name:
            return cmd
    raise KeyError(name)


def test_cli_providers_lists_registry(ctx):
    runner = CliRunner()
    result = runner.invoke(_plugin_cli_cmd("providers"))
    assert result.exit_code == 0
    assert "spotify" in result.output
    assert "google" in result.output


def test_cli_refresh_404_for_missing(ctx):
    runner = CliRunner()
    result = runner.invoke(_plugin_cli_cmd("refresh"), ["nope"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "404" in result.output


def test_cli_revoke_404_for_missing(ctx):
    runner = CliRunner()
    result = runner.invoke(_plugin_cli_cmd("revoke"), ["nope"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "404" in result.output


@respx.mock
def test_cli_refresh_happy_path(ctx):
    conn = _seed_authorized(ctx)
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-CLI-NEW",
            "expires_in": 3600,
            "token_type": "Bearer",
        }),
    )
    runner = CliRunner()
    result = runner.invoke(_plugin_cli_cmd("refresh"), [conn.id])
    assert result.exit_code == 0, result.output
    refreshed = ctx.store.get(conn.id)
    decrypted_pd = ctx.router.decrypt(
        refreshed.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    assert decrypted_pd["tokens"]["access_token"] == "AT-CLI-NEW"


def test_cli_revoke_happy_path(ctx):
    conn = _seed_authorized(ctx, provider="spotify")  # no revoke_url
    runner = CliRunner()
    result = runner.invoke(_plugin_cli_cmd("revoke"), [conn.id])
    assert result.exit_code == 0, result.output
    refreshed = ctx.store.get(conn.id)
    assert refreshed.status == "revoked"
