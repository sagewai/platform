# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OAuth error hierarchy tests."""
from __future__ import annotations

from sagewai.oauth.errors import (
    OAuthCallbackError,
    OAuthError,
    OAuthNotAuthorizedError,
    OAuthNotConfiguredError,
    OAuthRefreshError,
    OAuthScopeMissingError,
)


def test_all_subclasses_inherit_from_oauth_error():
    for cls in (
        OAuthNotConfiguredError,
        OAuthNotAuthorizedError,
        OAuthScopeMissingError,
        OAuthRefreshError,
        OAuthCallbackError,
    ):
        assert issubclass(cls, OAuthError)


def test_stable_error_codes():
    """Stable codes are the contract for admin UI rendering."""
    assert OAuthNotConfiguredError.code == "oauth_not_configured"
    assert OAuthNotAuthorizedError.code == "oauth_not_authorized"
    assert OAuthScopeMissingError.code == "oauth_scope_missing"
    assert OAuthRefreshError.code == "oauth_refresh_failed"
    assert OAuthCallbackError.code == "oauth_callback_error"
