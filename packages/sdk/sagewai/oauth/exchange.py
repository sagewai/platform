# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Token exchange + refresh transport against vendor OAuth endpoints.

Thin async HTTP layer: POSTs ``application/x-www-form-urlencoded`` to
``provider.token_url`` and returns the parsed JSON body. Callers
(vault helpers, the http executor's oauth2 branch) compute ``expires_at``
from the returned ``expires_in`` and persist the result.
"""
from __future__ import annotations

from typing import Any

import httpx

from sagewai.oauth.errors import OAuthCallbackError, OAuthRefreshError
from sagewai.oauth.providers import OAuthProvider


def _read_body(response: httpx.Response) -> Any:
    """Return parsed JSON body, falling back to text if the response is not JSON."""
    try:
        return response.json()
    except ValueError:
        return response.text


async def _post_token_request(
    provider: OAuthProvider,
    *,
    client_id: str,
    client_secret: str,
    form: dict[str, str],
) -> httpx.Response:
    """POST to ``provider.token_url`` with the right auth style.

    ``basic`` style → HTTP Basic ``Authorization`` header, credentials
    omitted from the body. ``body`` style → ``client_id`` / ``client_secret``
    added to the form data, no ``Authorization`` header.
    """
    if provider.token_auth_style == "basic":
        async with httpx.AsyncClient() as client:
            return await client.post(
                provider.token_url,
                data=form,
                auth=(client_id, client_secret),
            )
    # body style
    body = {**form, "client_id": client_id, "client_secret": client_secret}
    async with httpx.AsyncClient() as client:
        return await client.post(provider.token_url, data=body)


async def exchange_code(
    provider: OAuthProvider,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict:
    """Exchange authorization code for tokens via ``provider.token_url``.

    Returns the parsed JSON response from the vendor (typically containing
    ``access_token``, ``refresh_token``, ``expires_in``, ``scope``,
    ``token_type``). Caller computes ``expires_at`` from ``expires_in``.

    Raises :class:`OAuthCallbackError` on non-2xx response, with the
    vendor's error payload included in the exception message.
    """
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    response = await _post_token_request(
        provider,
        client_id=client_id,
        client_secret=client_secret,
        form=form,
    )
    if response.status_code >= 400:
        body = _read_body(response)
        raise OAuthCallbackError(
            f"token exchange failed: {response.status_code} {body}"
        )
    return response.json()


async def refresh_access_token(
    provider: OAuthProvider,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    """Refresh access token using a stored refresh_token.

    Returns the parsed JSON response from the vendor. ``refresh_token``
    may be absent in the response (e.g., Spotify's behavior — caller
    should keep the existing one in that case).

    Raises :class:`OAuthRefreshError` on non-2xx response.
    """
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    response = await _post_token_request(
        provider,
        client_id=client_id,
        client_secret=client_secret,
        form=form,
    )
    if response.status_code >= 400:
        body = _read_body(response)
        raise OAuthRefreshError(
            f"token refresh failed: {response.status_code} {body}"
        )
    return response.json()


__all__ = [
    "exchange_code",
    "refresh_access_token",
]
