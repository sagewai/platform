# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for OAuth2 refresh audit events + refresh_count persistence."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin.state_file import AdminStateFile
from sagewai.connections.bootstrap import build_connections_context
from sagewai.connections.protocols import oauth2 as oauth2_module
from sagewai.connections.protocols.oauth2 import (
    OAuth2ProtocolData,
    OAuth2ProtocolPlugin,
    _Tokens,
)
from sagewai.oauth import pending_auth


# ── Fixtures (replicated from test_protocols_oauth2_bodies.py) ───────
#
# The ctx / app fixtures + _seed_authorized helper live in
# test_protocols_oauth2_bodies.py, not a conftest. pytest fixtures
# can't be imported across modules, so we replicate the minimal set
# inline to keep this file self-contained.


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


# ── schema: refresh_count field ──────────────────────────────────────


def test_tokens_refresh_count_defaults_to_zero():
    t = _Tokens(
        access_token="AT",
        expires_at="2030-01-01T00:00:00+00:00",
        obtained_at="2026-01-01T00:00:00+00:00",
    )
    assert t.refresh_count == 0


def test_tokens_refresh_count_accepts_explicit_value():
    t = _Tokens(
        access_token="AT",
        expires_at="2030-01-01T00:00:00+00:00",
        obtained_at="2026-01-01T00:00:00+00:00",
        refresh_count=7,
    )
    assert t.refresh_count == 7


def test_tokens_refresh_count_rejects_negative():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _Tokens(
            access_token="AT",
            expires_at="2030-01-01T00:00:00+00:00",
            obtained_at="2026-01-01T00:00:00+00:00",
            refresh_count=-1,
        )


def test_protocol_data_with_tokens_carrying_refresh_count_validates():
    """OAuth2ProtocolData round-trips a tokens block with refresh_count."""
    pd = OAuth2ProtocolData(
        provider="spotify",
        client_id="id",
        client_secret="secret",
        redirect_uri="http://localhost/callback",
        requested_scopes=["user-read-private"],
        granted_scopes=["user-read-private"],
        tokens={
            "access_token": "AT",
            "refresh_token": "RT",
            "token_type": "Bearer",
            "expires_at": "2030-01-01T00:00:00+00:00",
            "obtained_at": "2026-01-01T00:00:00+00:00",
            "last_refreshed_at": None,
            "refresh_count": 3,
        },
    )
    assert pd.tokens.refresh_count == 3


# ── audit-emit helper ────────────────────────────────────────────────


def test_emit_refresh_event_success_shape(caplog):
    from sagewai.connections.protocols.oauth2 import _emit_refresh_event

    with caplog.at_level(logging.INFO, logger="sagewai.connections.protocols.oauth2"):
        _emit_refresh_event(
            success=True,
            connection_id="conn-1",
            project_id="proj",
            provider="spotify",
            trigger="api_call",
            old_expires_at="2026-01-01T00:00:00+00:00",
            new_expires_at="2026-01-01T01:00:00+00:00",
        )
    rec = next(r for r in caplog.records if getattr(r, "event", None) == "oauth2.token.refreshed")
    assert rec.connection_id == "conn-1"
    assert rec.project_id == "proj"
    assert rec.provider == "spotify"
    assert rec.trigger == "api_call"
    assert rec.old_expires_at == "2026-01-01T00:00:00+00:00"
    assert rec.new_expires_at == "2026-01-01T01:00:00+00:00"


def test_emit_refresh_event_failure_shape(caplog):
    from sagewai.connections.protocols.oauth2 import _emit_refresh_event

    with caplog.at_level(logging.INFO, logger="sagewai.connections.protocols.oauth2"):
        _emit_refresh_event(
            success=False,
            connection_id="conn-2",
            project_id="proj",
            provider="spotify",
            trigger="explicit",
            error_code="oauth_refresh_error",
            error_message="invalid_grant",
        )
    rec = next(r for r in caplog.records if getattr(r, "event", None) == "oauth2.token.refresh_failed")
    assert rec.connection_id == "conn-2"
    assert rec.error_code == "oauth_refresh_error"
    assert rec.error_message == "invalid_grant"
    assert rec.trigger == "explicit"


def test_emit_refresh_event_failure_omits_expiry_fields(caplog):
    from sagewai.connections.protocols.oauth2 import _emit_refresh_event

    with caplog.at_level(logging.INFO, logger="sagewai.connections.protocols.oauth2"):
        _emit_refresh_event(
            success=False,
            connection_id="conn-3",
            project_id="proj",
            provider="spotify",
            trigger="api_call",
            error_code="x",
            error_message="y",
        )
    rec = next(r for r in caplog.records if getattr(r, "event", None) == "oauth2.token.refresh_failed")
    # Failure events should not carry old/new expiry
    assert not hasattr(rec, "old_expires_at") or rec.old_expires_at is None


# ── get_default_access_token instrumentation ─────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_reactive_refresh_bumps_refresh_count(ctx):
    conn = _seed_authorized(ctx, expires_in_seconds=30, access_token="AT-OLD")
    ctx.store.set_default(conn.id)
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-REFRESHED",
            "expires_in": 3600,
            "token_type": "Bearer",
        }),
    )
    await OAuth2ProtocolPlugin.get_default_access_token(
        store=ctx.store, router=ctx.router, project_id="default", provider="spotify",
    )
    refreshed = ctx.store.get(conn.id)
    decrypted = ctx.router.decrypt(
        refreshed.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    assert decrypted["tokens"]["refresh_count"] == 1
    assert decrypted["tokens"]["last_refreshed_at"] is not None


@pytest.mark.asyncio
@respx.mock
async def test_reactive_refresh_emits_refreshed_event(ctx, caplog):
    conn = _seed_authorized(ctx, expires_in_seconds=30, access_token="AT-OLD")
    ctx.store.set_default(conn.id)
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-REFRESHED",
            "expires_in": 3600,
            "token_type": "Bearer",
        }),
    )
    with caplog.at_level(logging.INFO, logger="sagewai.connections.protocols.oauth2"):
        await OAuth2ProtocolPlugin.get_default_access_token(
            store=ctx.store, router=ctx.router, project_id="default", provider="spotify",
        )
    rec = next(r for r in caplog.records if getattr(r, "event", None) == "oauth2.token.refreshed")
    assert rec.provider == "spotify"
    assert rec.trigger == "api_call"  # default
    assert rec.new_expires_at is not None


@pytest.mark.asyncio
@respx.mock
async def test_reactive_refresh_trigger_kwarg_propagates(ctx, caplog):
    conn = _seed_authorized(ctx, expires_in_seconds=30, access_token="AT-OLD")
    ctx.store.set_default(conn.id)
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-REFRESHED",
            "expires_in": 3600,
            "token_type": "Bearer",
        }),
    )
    with caplog.at_level(logging.INFO, logger="sagewai.connections.protocols.oauth2"):
        await OAuth2ProtocolPlugin.get_default_access_token(
            store=ctx.store, router=ctx.router, project_id="default",
            provider="spotify", trigger="test",
        )
    rec = next(r for r in caplog.records if getattr(r, "event", None) == "oauth2.token.refreshed")
    assert rec.trigger == "test"


@pytest.mark.asyncio
@respx.mock
async def test_reactive_refresh_failure_emits_failed_event(ctx, caplog):
    from sagewai.oauth.errors import OAuthRefreshError

    conn = _seed_authorized(ctx, expires_in_seconds=30, access_token="AT-OLD")
    ctx.store.set_default(conn.id)
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"}),
    )
    with caplog.at_level(logging.INFO, logger="sagewai.connections.protocols.oauth2"):
        with pytest.raises(OAuthRefreshError):
            await OAuth2ProtocolPlugin.get_default_access_token(
                store=ctx.store, router=ctx.router, project_id="default", provider="spotify",
            )
    rec = next(r for r in caplog.records if getattr(r, "event", None) == "oauth2.token.refresh_failed")
    assert rec.provider == "spotify"


@pytest.mark.asyncio
@respx.mock
async def test_reactive_refresh_failure_does_not_bump_count(ctx):
    from sagewai.oauth.errors import OAuthRefreshError

    conn = _seed_authorized(ctx, expires_in_seconds=30, access_token="AT-OLD")
    ctx.store.set_default(conn.id)
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"}),
    )
    with pytest.raises(OAuthRefreshError):
        await OAuth2ProtocolPlugin.get_default_access_token(
            store=ctx.store, router=ctx.router, project_id="default", provider="spotify",
        )
    # Status is "expired"; refresh_count unchanged from seed (0).
    refreshed = ctx.store.get(conn.id)
    assert refreshed.status == "expired"
    decrypted = ctx.router.decrypt(
        refreshed.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    # The failure path never writes a new tokens dict — count + timestamp
    # must reflect the pre-refresh state. The seed predates refresh_count
    # (no key), proving the failure branch added nothing.
    assert decrypted["tokens"].get("refresh_count", 0) == 0
    assert decrypted["tokens"].get("last_refreshed_at") is None


# ── _refresh route (force-refresh) instrumentation ───────────────────


@respx.mock
def test_force_refresh_bumps_count_and_emits_event(app, ctx, caplog):
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
    with caplog.at_level(logging.INFO, logger="sagewai.connections.protocols.oauth2"):
        resp = app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/refresh")
    assert resp.status_code == 200, resp.text

    refreshed = ctx.store.get(conn.id)
    decrypted = ctx.router.decrypt(
        refreshed.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    assert decrypted["tokens"]["refresh_count"] == 1

    rec = next(r for r in caplog.records if getattr(r, "event", None) == "oauth2.token.refreshed")
    assert rec.trigger == "explicit"  # force-refresh path labels itself explicit
    assert rec.provider == "spotify"


@respx.mock
def test_force_refresh_failure_emits_failed_event(app, ctx, caplog):
    conn = _seed_authorized(ctx)
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"}),
    )
    with caplog.at_level(logging.INFO, logger="sagewai.connections.protocols.oauth2"):
        resp = app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/refresh")
    assert resp.status_code in (400, 422, 500)

    rec = next(r for r in caplog.records if getattr(r, "event", None) == "oauth2.token.refresh_failed")
    assert rec.trigger == "explicit"
    assert rec.provider == "spotify"


@respx.mock
def test_force_refresh_increments_existing_count(app, ctx):
    """A connection that already refreshed once should reach count=2."""
    conn = _seed_authorized(ctx)
    # First refresh
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "AT-1", "refresh_token": "RT-1",
            "expires_in": 3600, "token_type": "Bearer",
        }),
    )
    app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/refresh")
    # Second refresh
    app.post(f"/api/v1/admin/connections/oauth2/{conn.id}/refresh")

    refreshed = ctx.store.get(conn.id)
    decrypted = ctx.router.decrypt(
        refreshed.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    assert decrypted["tokens"]["refresh_count"] == 2


# ── public_view / CLI flow-through ───────────────────────────────────


def test_refresh_count_survives_public_view():
    """public_view must keep refresh_count (it's non-sensitive metadata)."""
    plugin = OAuth2ProtocolPlugin()
    pd = {
        "provider": "spotify", "client_id": "id", "client_secret": "secret",
        "redirect_uri": "http://localhost/callback",
        "requested_scopes": ["user-read-private"], "granted_scopes": [],
        "tokens": {
            "access_token": "AT", "refresh_token": "RT", "token_type": "Bearer",
            "expires_at": "2030-01-01T00:00:00+00:00",
            "obtained_at": "2026-01-01T00:00:00+00:00",
            "last_refreshed_at": "2026-05-29T00:00:00+00:00", "refresh_count": 9,
        },
    }
    masked = plugin.public_view(pd)
    assert masked["tokens"]["refresh_count"] == 9
    # And the secret is still masked
    assert masked["tokens"]["access_token"] != "AT"
