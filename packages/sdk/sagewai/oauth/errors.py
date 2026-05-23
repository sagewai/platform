# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OAuth error hierarchy.

Each exception carries a stable ``code`` class attribute consumed by
the admin UI to render actionable links (e.g. ``"Connect Spotify →"``).
"""
from __future__ import annotations


class OAuthError(Exception):
    """Base class for all OAuth-related failures."""

    code = "oauth_error"


class OAuthNotConfiguredError(OAuthError):
    """No OAuth client record exists for the requested provider in this project."""

    code = "oauth_not_configured"


class OAuthNotAuthorizedError(OAuthError):
    """An OAuth client exists but its status is not ``authorized``."""

    code = "oauth_not_authorized"


class OAuthScopeMissingError(OAuthError):
    """Tool requires scopes the OAuth client has not been granted."""

    code = "oauth_scope_missing"


class OAuthRefreshError(OAuthError):
    """Token refresh failed; the client is marked ``expired`` and requires re-authorization."""

    code = "oauth_refresh_failed"


class OAuthCallbackError(OAuthError):
    """Vendor callback returned an error or invalid state."""

    code = "oauth_callback_error"


__all__ = [
    "OAuthError",
    "OAuthNotConfiguredError",
    "OAuthNotAuthorizedError",
    "OAuthScopeMissingError",
    "OAuthRefreshError",
    "OAuthCallbackError",
]
