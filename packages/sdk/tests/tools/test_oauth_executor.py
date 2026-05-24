# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""HTTP-executor oauth2 branch — eager + reactive refresh + scope/error mapping.

PR4 migrated the executor from ``sagewai.oauth.vault`` to the
``OAuth2ProtocolPlugin.get_default_access_token`` helper which talks
to the generic ConnectionStore + CredentialsBackendRouter. These tests
seed connections via the new store and exercise the same behavior:

- happy path → bearer-token injection
- pre-emptive refresh (< 60s to expiry) → vendor token endpoint hit once
- reactive refresh (server 401 → refresh → retry)
- refresh failure → ``status='expired'`` + ``OAuthRefreshError``
- ``OAuthNotConfiguredError`` (no client seeded)
- ``OAuthNotAuthorizedError`` (client pending, never authorized)
- ``OAuthScopeMissingError`` (catalog requires scope not granted)
- ``insufficient_scope`` 401 → ``OAuthScopeMissingError`` (no refresh attempt)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from sagewai.connections.bootstrap import build_connections_context
from sagewai.connections.protocols.oauth2 import OAuth2ProtocolPlugin
from sagewai.oauth.errors import (
    OAuthNotAuthorizedError,
    OAuthNotConfiguredError,
    OAuthRefreshError,
    OAuthScopeMissingError,
)
from sagewai.tools.executors import http as http_executor
from sagewai.tools.registry import CatalogEntry


def _spotify_entry(
    required_scopes: list[str] | None = None,
) -> CatalogEntry:
    return CatalogEntry(
        id="spotify_test",
        version="0.1.0",
        title="Spotify",
        description="test",
        category="music",
        kind="http",
        sandbox_tier="SANDBOXED",
        exec_={
            "http": {
                "base_url": "https://api.spotify.com/v1",
                "auth": {"kind": "oauth2", "oauth_provider": "spotify"},
                "operations": {
                    "current_user_profile": {"method": "GET", "path": "/me"},
                },
            }
        },
        scopes=frozenset(["network.outbound.fetch", "secrets.oauth.spotify"]),
        setup={
            "auth_complexity": "oauth2",
            "oauth_provider": "spotify",
            "required_scopes": required_scopes or ["user-read-private"],
            "body": "test",
        },
        schemas={},
    )


def _seed_authorized_client(
    ctx,
    *,
    granted_scopes: tuple[str, ...] = ("user-read-private",),
    expires_in_seconds: int = 3600,
    refresh_token: str = "RT-INITIAL",
    access_token: str = "AT-INITIAL",
):
    """Create + authorize a Spotify oauth2 connection via the generic store."""
    pd = {
        "provider": "spotify",
        "client_id": "CID",
        "client_secret": "SEC",
        "redirect_uri": "http://localhost/cb",
        "requested_scopes": list(granted_scopes),
        "granted_scopes": list(granted_scopes),
        "tokens": None,
    }
    encrypted_pd = ctx.router.encrypt(
        pd,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    conn = ctx.store.create(
        protocol="oauth2",
        project_id="default",
        display_name="Test",
        tags=[],
        protocol_data=encrypted_pd,
    )
    now = datetime.now(timezone.utc)
    decrypted_pd = ctx.router.decrypt(
        conn.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    pd_with_tokens = {
        **decrypted_pd,
        "granted_scopes": list(granted_scopes),
        "tokens": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_at": (now + timedelta(seconds=expires_in_seconds)).isoformat(),
            "obtained_at": now.isoformat(),
            "last_refreshed_at": None,
        },
    }
    encrypted_with_tokens = ctx.router.encrypt(
        pd_with_tokens,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    ctx.store.update(conn.id, protocol_data=encrypted_with_tokens, status="authorized")
    return conn.id


def _seed_pending_client(ctx) -> str:
    pd = {
        "provider": "spotify",
        "client_id": "CID",
        "client_secret": "SEC",
        "redirect_uri": "http://localhost/cb",
        "requested_scopes": ["user-read-private"],
        "granted_scopes": [],
        "tokens": None,
    }
    encrypted_pd = ctx.router.encrypt(
        pd,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    conn = ctx.store.create(
        protocol="oauth2",
        project_id="default",
        display_name="Test",
        tags=[],
        protocol_data=encrypted_pd,
    )
    return conn.id


@pytest.fixture()
def ctx_env(tmp_path: Path, monkeypatch) -> Path:
    """Env wiring: SAGEWAI_MASTER_KEY + SAGEWAI_CONNECTIONS_FILE + ADMIN_STATE_FILE.

    The executor calls ``build_connections_context(AdminStateFile(default_admin_state_path()))``
    internally — those resolutions read these env vars.
    """
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(tmp_path / "admin-state.json"))
    return tmp_path


@pytest.fixture()
def ctx(ctx_env):
    from sagewai.admin.state_file import AdminStateFile
    sf = AdminStateFile(ctx_env / "admin-state.json")
    return build_connections_context(sf)


def _no_creds(*, project_id: str, kind: str, id: str) -> dict[str, str]:
    """Stub get_credentials — oauth2 doesn't read from the tool credential vault."""
    return {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_happy_path_injects_bearer_token(ctx) -> None:
    _seed_authorized_client(ctx)

    token_route = respx.post("https://accounts.spotify.com/api/token")
    me_route = respx.get("https://api.spotify.com/v1/me").respond(
        200, json={"id": "user1"}
    )

    out = await http_executor.run(
        _spotify_entry(),
        operation="current_user_profile",
        inputs={},
        project_id="default",
        get_credentials=_no_creds,
    )

    assert out == {"id": "user1"}
    assert me_route.calls.last.request.headers["Authorization"] == "Bearer AT-INITIAL"
    assert not token_route.called  # no refresh needed


@pytest.mark.asyncio
@respx.mock
async def test_preemptive_refresh_when_near_expiry(ctx) -> None:
    conn_id = _seed_authorized_client(ctx, expires_in_seconds=30)

    token_route = respx.post("https://accounts.spotify.com/api/token").respond(
        200,
        json={
            "access_token": "AT-REFRESHED",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    me_route = respx.get("https://api.spotify.com/v1/me").respond(
        200, json={"id": "user1"}
    )

    out = await http_executor.run(
        _spotify_entry(),
        operation="current_user_profile",
        inputs={},
        project_id="default",
        get_credentials=_no_creds,
    )

    assert out == {"id": "user1"}
    assert token_route.call_count == 1
    assert me_route.calls.last.request.headers["Authorization"] == "Bearer AT-REFRESHED"

    # Store now holds the refreshed token (encrypted).
    refreshed = ctx.store.get(conn_id)
    decrypted_pd = ctx.router.decrypt(
        refreshed.protocol_data,
        sensitive_field_paths=OAuth2ProtocolPlugin.sensitive_fields,
        connection_credentials_backend=None,
    )
    assert decrypted_pd["tokens"]["access_token"] == "AT-REFRESHED"


@pytest.mark.asyncio
@respx.mock
async def test_reactive_refresh_on_401(ctx) -> None:
    _seed_authorized_client(ctx)

    token_route = respx.post("https://accounts.spotify.com/api/token").respond(
        200,
        json={
            "access_token": "AT-REFRESHED",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    me_route = respx.get("https://api.spotify.com/v1/me").mock(
        side_effect=[
            httpx.Response(401, json={"error": "expired_token"}),
            httpx.Response(200, json={"id": "user1"}),
        ]
    )

    out = await http_executor.run(
        _spotify_entry(),
        operation="current_user_profile",
        inputs={},
        project_id="default",
        get_credentials=_no_creds,
    )

    assert out == {"id": "user1"}
    assert me_route.call_count == 2
    assert token_route.call_count == 1
    second_call_auth = me_route.calls[1].request.headers["Authorization"]
    assert second_call_auth == "Bearer AT-REFRESHED"


@pytest.mark.asyncio
@respx.mock
async def test_refresh_fails_marks_expired_and_raises(ctx) -> None:
    conn_id = _seed_authorized_client(ctx)

    respx.post("https://accounts.spotify.com/api/token").respond(
        400, json={"error": "invalid_grant"}
    )
    respx.get("https://api.spotify.com/v1/me").respond(
        401, json={"error": "expired_token"}
    )

    with pytest.raises(OAuthRefreshError):
        await http_executor.run(
            _spotify_entry(),
            operation="current_user_profile",
            inputs={},
            project_id="default",
            get_credentials=_no_creds,
        )

    refreshed = ctx.store.get(conn_id)
    assert refreshed.status == "expired"


@pytest.mark.asyncio
@respx.mock
async def test_oauth_not_configured_error(ctx) -> None:
    # No client seeded.
    token_route = respx.post("https://accounts.spotify.com/api/token")
    me_route = respx.get("https://api.spotify.com/v1/me")

    with pytest.raises(OAuthNotConfiguredError):
        await http_executor.run(
            _spotify_entry(),
            operation="current_user_profile",
            inputs={},
            project_id="default",
            get_credentials=_no_creds,
        )

    assert not token_route.called
    assert not me_route.called


@pytest.mark.asyncio
@respx.mock
async def test_oauth_not_authorized_error(ctx) -> None:
    _seed_pending_client(ctx)

    token_route = respx.post("https://accounts.spotify.com/api/token")
    me_route = respx.get("https://api.spotify.com/v1/me")

    with pytest.raises(OAuthNotAuthorizedError):
        await http_executor.run(
            _spotify_entry(),
            operation="current_user_profile",
            inputs={},
            project_id="default",
            get_credentials=_no_creds,
        )

    assert not token_route.called
    assert not me_route.called


@pytest.mark.asyncio
@respx.mock
async def test_oauth_scope_missing_error(ctx) -> None:
    # Granted scopes don't include the entry's required scope.
    _seed_authorized_client(ctx, granted_scopes=("user-read-private",))

    token_route = respx.post("https://accounts.spotify.com/api/token")
    me_route = respx.get("https://api.spotify.com/v1/me")

    with pytest.raises(OAuthScopeMissingError):
        await http_executor.run(
            _spotify_entry(required_scopes=["playlist-modify-public"]),
            operation="current_user_profile",
            inputs={},
            project_id="default",
            get_credentials=_no_creds,
        )

    assert not token_route.called
    assert not me_route.called


@pytest.mark.asyncio
@respx.mock
async def test_insufficient_scope_401_raises_scope_missing(ctx) -> None:
    _seed_authorized_client(ctx)

    token_route = respx.post("https://accounts.spotify.com/api/token")
    me_route = respx.get("https://api.spotify.com/v1/me").respond(
        401,
        json={"error": "insufficient_scope"},
        headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"'},
    )

    with pytest.raises(OAuthScopeMissingError):
        await http_executor.run(
            _spotify_entry(),
            operation="current_user_profile",
            inputs={},
            project_id="default",
            get_credentials=_no_creds,
        )

    # Single /me call, no refresh.
    assert me_route.call_count == 1
    assert not token_route.called
