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

Covers the oauth2 path added in Task 8:

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

from sagewai.oauth import vault
from sagewai.oauth.errors import (
    OAuthNotAuthorizedError,
    OAuthNotConfiguredError,
    OAuthRefreshError,
    OAuthScopeMissingError,
)
from sagewai.sealed.crypto import Crypto
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
    store_path: Path,
    crypto: Crypto,
    *,
    granted_scopes: tuple[str, ...] = ("user-read-private",),
    expires_in_seconds: int = 3600,
    refresh_token: str = "RT-INITIAL",
    access_token: str = "AT-INITIAL",
) -> str:
    rec = vault.create_client(
        store_path,
        crypto,
        provider="spotify",
        project_id="default",
        display_name="Test",
        client_id="CID",
        client_secret="SEC",
        redirect_uri="http://localhost/cb",
        requested_scopes=list(granted_scopes),
    )
    now = datetime.now(timezone.utc)
    vault.update_tokens(
        store_path,
        crypto,
        rec["id"],
        tokens={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_at": (
                now + timedelta(seconds=expires_in_seconds)
            ).isoformat(),
            "obtained_at": now.isoformat(),
            "last_refreshed_at": None,
        },
        granted_scopes=list(granted_scopes),
        status="authorized",
    )
    return rec["id"]


def _seed_pending_client(store_path: Path, crypto: Crypto) -> str:
    """Create a client without ever calling update_tokens — status stays ``pending``."""
    rec = vault.create_client(
        store_path,
        crypto,
        provider="spotify",
        project_id="default",
        display_name="Test",
        client_id="CID",
        client_secret="SEC",
        redirect_uri="http://localhost/cb",
        requested_scopes=["user-read-private"],
    )
    return rec["id"]


@pytest.fixture()
def crypto() -> Crypto:
    return Crypto(Fernet.generate_key())


@pytest.fixture()
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "store.json"


@pytest.fixture(autouse=True)
def _wire_executor_to_test_store(
    monkeypatch: pytest.MonkeyPatch,
    store_path: Path,
    crypto: Crypto,
) -> None:
    """Inject the per-test store path + crypto into the executor.

    The executor resolves the store path via ``vault._store_path()`` and the
    master key via ``_resolve_crypto()``. Patching these two seams keeps the
    test isolated from the user's real ``~/.sagewai`` state.
    """
    monkeypatch.setattr(
        http_executor, "_resolve_store_path", lambda: store_path
    )
    monkeypatch.setattr(http_executor, "_resolve_crypto", lambda: crypto)


def _no_creds(*, project_id: str, kind: str, id: str) -> dict[str, str]:
    """Stub get_credentials — oauth2 doesn't read from the tool credential vault."""
    return {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_happy_path_injects_bearer_token(
    store_path: Path, crypto: Crypto
) -> None:
    _seed_authorized_client(store_path, crypto)

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
async def test_preemptive_refresh_when_near_expiry(
    store_path: Path, crypto: Crypto
) -> None:
    client_id = _seed_authorized_client(
        store_path, crypto, expires_in_seconds=30
    )

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

    # Vault now stores the refreshed access token.
    decrypted = vault.get_client_with_secrets(store_path, client_id, crypto)
    assert decrypted is not None
    assert decrypted["tokens"]["access_token"] == "AT-REFRESHED"


@pytest.mark.asyncio
@respx.mock
async def test_reactive_refresh_on_401(
    store_path: Path, crypto: Crypto
) -> None:
    _seed_authorized_client(store_path, crypto)

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
    # The second /me call carried the refreshed token.
    second_call_auth = me_route.calls[1].request.headers["Authorization"]
    assert second_call_auth == "Bearer AT-REFRESHED"


@pytest.mark.asyncio
@respx.mock
async def test_refresh_fails_marks_expired_and_raises(
    store_path: Path, crypto: Crypto
) -> None:
    client_id = _seed_authorized_client(store_path, crypto)

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

    # Vault row marked expired.
    masked = vault.get_client(store_path, client_id)
    assert masked is not None
    assert masked["status"] == "expired"


@pytest.mark.asyncio
@respx.mock
async def test_oauth_not_configured_error(
    store_path: Path, crypto: Crypto
) -> None:
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
async def test_oauth_not_authorized_error(
    store_path: Path, crypto: Crypto
) -> None:
    _seed_pending_client(store_path, crypto)

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
async def test_oauth_scope_missing_error(
    store_path: Path, crypto: Crypto
) -> None:
    # Granted scopes don't include the entry's required scope.
    _seed_authorized_client(
        store_path, crypto, granted_scopes=("user-read-private",)
    )

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
async def test_insufficient_scope_401_raises_scope_missing(
    store_path: Path, crypto: Crypto
) -> None:
    _seed_authorized_client(store_path, crypto)

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
