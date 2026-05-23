# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Token-exchange / refresh transport tests against mocked vendor endpoints."""
from __future__ import annotations

import base64

import httpx
import pytest
import respx

from sagewai.oauth.errors import OAuthCallbackError, OAuthRefreshError
from sagewai.oauth.exchange import exchange_code, refresh_access_token
from sagewai.oauth.providers import get_provider


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_spotify_uses_basic_auth():
    spotify = get_provider("spotify")
    route = respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 3600,
                "scope": "user-read-private",
                "token_type": "Bearer",
            },
        ),
    )

    result = await exchange_code(
        spotify,
        client_id="CID",
        client_secret="SEC",
        code="auth-code",
        redirect_uri="http://localhost/cb",
        code_verifier="verifier",
    )

    assert result["access_token"] == "AT"
    assert result["refresh_token"] == "RT"
    assert route.called
    req = route.calls.last.request

    expected = base64.b64encode(b"CID:SEC").decode()
    assert req.headers.get("Authorization") == f"Basic {expected}"

    body = dict(httpx.QueryParams(req.content.decode()))
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "auth-code"
    assert body["redirect_uri"] == "http://localhost/cb"
    assert body["code_verifier"] == "verifier"
    # Basic auth → credentials must NOT appear in the body
    assert "client_id" not in body
    assert "client_secret" not in body


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_google_uses_body_auth():
    google = get_provider("google")
    route = respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 3600,
                "scope": "openid email",
                "token_type": "Bearer",
            },
        ),
    )

    result = await exchange_code(
        google,
        client_id="CID",
        client_secret="SEC",
        code="auth-code",
        redirect_uri="http://localhost/cb",
        code_verifier="verifier",
    )

    assert result["access_token"] == "AT"
    assert route.called
    req = route.calls.last.request

    # Body auth → no Authorization header
    assert req.headers.get("Authorization") is None

    body = dict(httpx.QueryParams(req.content.decode()))
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "auth-code"
    assert body["redirect_uri"] == "http://localhost/cb"
    assert body["code_verifier"] == "verifier"
    assert body["client_id"] == "CID"
    assert body["client_secret"] == "SEC"


@pytest.mark.asyncio
@respx.mock
async def test_refresh_access_token_happy_path():
    spotify = get_provider("spotify")
    route = respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "AT2",
                "expires_in": 3600,
                "scope": "user-read-private",
                "token_type": "Bearer",
            },
        ),
    )

    result = await refresh_access_token(
        spotify,
        client_id="CID",
        client_secret="SEC",
        refresh_token="RT",
    )

    assert result["access_token"] == "AT2"
    # Spotify omits refresh_token on refresh — caller keeps existing one.
    assert "refresh_token" not in result
    assert route.called
    req = route.calls.last.request

    expected = base64.b64encode(b"CID:SEC").decode()
    assert req.headers.get("Authorization") == f"Basic {expected}"

    body = dict(httpx.QueryParams(req.content.decode()))
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "RT"


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_vendor_error_raises_callback_error():
    spotify = get_provider("spotify")
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"}),
    )

    with pytest.raises(OAuthCallbackError) as excinfo:
        await exchange_code(
            spotify,
            client_id="CID",
            client_secret="SEC",
            code="bad-code",
            redirect_uri="http://localhost/cb",
            code_verifier="verifier",
        )

    assert "invalid_grant" in str(excinfo.value)


@pytest.mark.asyncio
@respx.mock
async def test_refresh_vendor_error_raises_refresh_error():
    spotify = get_provider("spotify")
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"}),
    )

    with pytest.raises(OAuthRefreshError) as excinfo:
        await refresh_access_token(
            spotify,
            client_id="CID",
            client_secret="SEC",
            refresh_token="stale-rt",
        )

    assert "invalid_grant" in str(excinfo.value)
