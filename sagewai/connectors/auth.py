# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Credential resolution and OAuth2 client for connectors."""

from __future__ import annotations

import os
import secrets
import urllib.parse
from typing import TYPE_CHECKING

import httpx

from sagewai.connectors.base import TokenSet

if TYPE_CHECKING:
    from sagewai.connectors.base import ConnectorSpec
    from sagewai.connectors.stores import CredentialStore, OAuthTokenStore


class CredentialResolver:
    """Resolves credentials for a connector.

    Priority: environment variables > database > vault (future).
    """

    def __init__(self, store: CredentialStore | None = None) -> None:
        self._store = store

    async def resolve(self, connector: ConnectorSpec) -> dict[str, str]:
        """Resolve credentials for a connector from available sources."""
        # Start with DB values (if store available)
        result: dict[str, str] = {}
        if self._store:
            stored = await self._store.get(connector.name)
            if stored:
                result.update(stored)

        # Env vars override DB values
        for field in connector.auth_fields:
            env_val = os.environ.get(field.env_var)
            if env_val:
                result[field.key] = env_val

        return result


class OAuth2Client:
    """Handles server-side OAuth2 authorization code flow.

    Works with connectors that declare auth_type=OAUTH2 and provide
    oauth_authorize_url, oauth_token_url, and oauth_scopes.
    """

    def __init__(self, token_store: OAuthTokenStore | None = None) -> None:
        self._token_store = token_store
        self._pending_states: dict[str, dict] = {}

    async def get_authorization_url(
        self,
        connector: ConnectorSpec,
        redirect_uri: str,
        scopes: list[str] | None = None,
    ) -> tuple[str, str]:
        """Generate OAuth2 authorization URL + state token."""
        if not connector.oauth_authorize_url:
            raise ValueError(f"Connector '{connector.name}' has no oauth_authorize_url")

        state = secrets.token_urlsafe(32)
        self._pending_states[state] = {
            "connector_name": connector.name,
            "redirect_uri": redirect_uri,
        }

        client_id = ""
        for field in connector.auth_fields:
            if field.key == "client_id":
                client_id = os.environ.get(field.env_var, "")
                break

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": " ".join(scopes or connector.oauth_scopes or []),
        }
        url = f"{connector.oauth_authorize_url}?{urllib.parse.urlencode(params)}"
        return url, state

    async def exchange_code(
        self,
        connector: ConnectorSpec,
        code: str,
        state: str,
    ) -> TokenSet:
        """Exchange authorization code for tokens."""
        if state not in self._pending_states:
            raise ValueError("Invalid or expired state token")

        pending = self._pending_states.pop(state)

        client_id = client_secret = ""
        for field in connector.auth_fields:
            if field.key == "client_id":
                client_id = os.environ.get(field.env_var, "")
            elif field.key == "client_secret":
                client_secret = os.environ.get(field.env_var, "")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                connector.oauth_token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": pending["redirect_uri"],
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        token_set = TokenSet(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
        )

        if self._token_store:
            await self._token_store.save_token(connector.name, token_set)

        return token_set

    async def refresh_token(
        self,
        connector: ConnectorSpec,
        token_set: TokenSet,
    ) -> TokenSet:
        """Refresh an expired access token."""
        if not token_set.refresh_token:
            raise ValueError("No refresh token available")

        client_id = client_secret = ""
        for field in connector.auth_fields:
            if field.key == "client_id":
                client_id = os.environ.get(field.env_var, "")
            elif field.key == "client_secret":
                client_secret = os.environ.get(field.env_var, "")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                connector.oauth_token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": token_set.refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        new_token = TokenSet(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", token_set.refresh_token),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
        )

        if self._token_store:
            await self._token_store.save_token(connector.name, new_token)

        return new_token
