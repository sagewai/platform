# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Provider registry tests — spotify + google entries are well-formed."""
from __future__ import annotations

import pytest

from sagewai.oauth.providers import (
    OAuthProvider,
    UnknownProviderError,
    all_providers,
    get_provider,
)


def test_spotify_entry_fields():
    p = get_provider("spotify")
    assert isinstance(p, OAuthProvider)
    assert p.id == "spotify"
    assert p.display_name == "Spotify"
    assert p.authorize_url.startswith("https://")
    assert p.token_url.startswith("https://")
    assert p.pkce_required is True
    assert p.pkce_method == "S256"
    assert p.scope_separator == " "
    assert p.token_auth_style == "basic"
    assert len(p.default_scopes) >= 1


def test_google_entry_fields():
    p = get_provider("google")
    assert p.id == "google"
    assert p.authorize_url.startswith("https://accounts.google.com/")
    assert p.token_url.startswith("https://oauth2.googleapis.com/")
    assert p.pkce_required is False  # supported but not strictly required
    assert p.pkce_method == "S256"
    assert p.token_auth_style == "body"


def test_all_providers_returns_both():
    ids = {p.id for p in all_providers()}
    assert "spotify" in ids
    assert "google" in ids


def test_unknown_provider_raises():
    with pytest.raises(UnknownProviderError):
        get_provider("not-a-real-provider")


def test_providers_use_https_only():
    """No plaintext OAuth endpoints allowed."""
    for p in all_providers():
        assert p.authorize_url.startswith("https://"), p.id
        assert p.token_url.startswith("https://"), p.id
        if p.revoke_url is not None:
            assert p.revoke_url.startswith("https://"), p.id
