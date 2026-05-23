# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OAuth2 authorization-code flow infrastructure.

Provides the provider registry, error hierarchy, PKCE helpers, pending-auth
state, vault helpers, and token exchange/refresh logic used by the http
executor's ``auth.kind: oauth2`` branch, the admin OAuth routes, and the
``sagewai oauth`` CLI command group.
"""
from sagewai.oauth.errors import (
    OAuthCallbackError,
    OAuthError,
    OAuthNotAuthorizedError,
    OAuthNotConfiguredError,
    OAuthRefreshError,
    OAuthScopeMissingError,
)
from sagewai.oauth.exchange import exchange_code, refresh_access_token
from sagewai.oauth.providers import (
    OAuthProvider,
    UnknownProviderError,
    all_providers,
    get_provider,
)

__all__ = [
    "OAuthCallbackError",
    "OAuthError",
    "OAuthNotAuthorizedError",
    "OAuthNotConfiguredError",
    "OAuthProvider",
    "OAuthRefreshError",
    "OAuthScopeMissingError",
    "UnknownProviderError",
    "all_providers",
    "exchange_code",
    "get_provider",
    "refresh_access_token",
]
