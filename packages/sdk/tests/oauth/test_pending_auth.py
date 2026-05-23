# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pending-auth state store tests."""
from __future__ import annotations

from sagewai.oauth.pending_auth import PendingAuthEntry, PendingAuthStore


def test_put_then_pop_roundtrip():
    store = PendingAuthStore(ttl_seconds=600)
    entry = PendingAuthEntry(
        oauth_client_id="abc",
        code_verifier="v",
        redirect_uri="http://example/cb",
    )
    store.put("state-1", entry)
    got = store.pop("state-1")
    assert got is not None
    assert got.oauth_client_id == "abc"
    assert got.code_verifier == "v"
    assert got.redirect_uri == "http://example/cb"


def test_pop_is_single_use():
    store = PendingAuthStore(ttl_seconds=600)
    store.put("state-1", PendingAuthEntry("a", "b", "c"))
    assert store.pop("state-1") is not None
    assert store.pop("state-1") is None


def test_ttl_expired_entry_returns_none(monkeypatch):
    store = PendingAuthStore(ttl_seconds=10)
    import sagewai.oauth.pending_auth as pa

    base_time = pa.time.time()
    store.put("s", PendingAuthEntry("a", "b", "c"))
    monkeypatch.setattr(pa.time, "time", lambda: base_time + 11)
    assert store.pop("s") is None


def test_missing_state_returns_none():
    store = PendingAuthStore(ttl_seconds=600)
    assert store.pop("never-stored") is None
