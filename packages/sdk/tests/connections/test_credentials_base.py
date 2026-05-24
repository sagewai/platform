# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Credentials backend Protocol + dotted-path helpers."""
from __future__ import annotations

from sagewai.connections.credentials.base import (
    CredentialsBackend,
    _get_path,
    _set_path,
)
from sagewai.connections.models import HealthResult


def test_credentials_backend_is_runtime_checkable_protocol():
    """Plugins are duck-typed via runtime_checkable Protocol."""
    assert getattr(CredentialsBackend, "_is_protocol", False) is True
    assert getattr(CredentialsBackend, "_is_runtime_protocol", False) is True


def test_get_path_top_level():
    d = {"client_secret": "abc"}
    assert _get_path(d, "client_secret") == "abc"


def test_get_path_nested():
    d = {"tokens": {"access_token": "AT", "refresh_token": "RT"}}
    assert _get_path(d, "tokens.access_token") == "AT"
    assert _get_path(d, "tokens.refresh_token") == "RT"


def test_get_path_missing_returns_none():
    d = {"tokens": None}
    assert _get_path(d, "tokens.access_token") is None
    assert _get_path(d, "nope") is None


def test_get_path_intermediate_not_a_dict_returns_none():
    d = {"tokens": "not-a-dict"}
    assert _get_path(d, "tokens.access_token") is None


def test_set_path_top_level_returns_deep_copy():
    d = {"client_secret": "old"}
    out = _set_path(d, "client_secret", "new")
    assert out["client_secret"] == "new"
    assert d["client_secret"] == "old"  # original untouched


def test_set_path_nested():
    d = {"tokens": {"access_token": "AT", "refresh_token": "RT"}}
    out = _set_path(d, "tokens.access_token", "AT_NEW")
    assert out["tokens"]["access_token"] == "AT_NEW"
    assert out["tokens"]["refresh_token"] == "RT"  # sibling preserved


def test_set_path_only_updates_existing_leaves():
    """If the leaf path doesn't exist, the data is returned unchanged.

    Encryption only applies to fields the plugin already wrote — a
    pending oauth2 record has no `tokens.access_token` to encrypt.
    """
    d = {"client_secret": "abc"}  # no tokens dict
    out = _set_path(d, "tokens.access_token", "X")
    assert out == {"client_secret": "abc"}  # no `tokens` added


def test_set_path_only_updates_existing_intermediate_dicts():
    d = {"client_secret": "abc", "tokens": None}
    out = _set_path(d, "tokens.access_token", "X")
    assert out == {"client_secret": "abc", "tokens": None}  # `tokens` is None, no setting
