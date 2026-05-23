# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Static registry of supported OAuth2 providers.

Operators add OAuth clients (their own client_id/secret per provider);
the providers themselves — authorize_url, token_url, PKCE policy, scope
separator, token auth style — are platform-controlled and reviewed
alongside the tools that use them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class UnknownProviderError(KeyError):
    """Lookup for a provider id that isn't in the registry."""


@dataclass(frozen=True)
class OAuthProvider:
    id: str
    display_name: str
    authorize_url: str
    token_url: str
    revoke_url: str | None
    pkce_required: bool
    pkce_method: Literal["S256", "plain", None]
    scope_separator: str
    token_auth_style: Literal["basic", "body"]
    default_scopes: tuple[str, ...]
    docs_url: str


_PROVIDERS: tuple[OAuthProvider, ...] = (
    OAuthProvider(
        id="spotify",
        display_name="Spotify",
        authorize_url="https://accounts.spotify.com/authorize",
        token_url="https://accounts.spotify.com/api/token",
        revoke_url=None,  # Spotify has no revoke endpoint; operator revokes in dashboard
        pkce_required=True,
        pkce_method="S256",
        scope_separator=" ",
        token_auth_style="basic",
        default_scopes=(
            "user-read-private",
            "playlist-read-private",
        ),
        docs_url="https://developer.spotify.com/documentation/web-api/concepts/authorization",
    ),
    OAuthProvider(
        id="google",
        display_name="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        revoke_url="https://oauth2.googleapis.com/revoke",
        pkce_required=False,
        pkce_method="S256",
        scope_separator=" ",
        token_auth_style="body",
        default_scopes=(
            "openid",
            "email",
        ),
        docs_url="https://developers.google.com/identity/protocols/oauth2/web-server",
    ),
)

_BY_ID = {p.id: p for p in _PROVIDERS}


def get_provider(provider_id: str) -> OAuthProvider:
    """Look up a provider by id; raises ``UnknownProviderError`` if absent."""
    try:
        return _BY_ID[provider_id]
    except KeyError as e:
        raise UnknownProviderError(provider_id) from e


def all_providers() -> tuple[OAuthProvider, ...]:
    """Return every registered provider in declaration order."""
    return _PROVIDERS


__all__ = [
    "OAuthProvider",
    "UnknownProviderError",
    "all_providers",
    "get_provider",
]
