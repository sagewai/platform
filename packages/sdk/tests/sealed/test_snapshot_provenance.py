# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Sealed-iii.C — _snapshot_secret_provenance backend lookup."""
from __future__ import annotations

import hashlib

import pytest

from sagewai.core.state import _snapshot_secret_provenance


async def test_snapshot_provenance_no_profile_returns_empty():
    h, v, r = await _snapshot_secret_provenance(None, ["A", "B"])
    assert h == {} and v == {} and r == {}


async def test_snapshot_provenance_no_keys_returns_empty():
    h, v, r = await _snapshot_secret_provenance("builtin://test", [])
    assert h == {} and v == {} and r == {}


async def test_snapshot_provenance_returns_value_hashes(tmp_path, monkeypatch):
    """With a real builtin backend seeded with two secrets, the helper
    returns SHA-256 hex of each value, version_ids all None, and no
    revocations in the dict."""
    from cryptography.fernet import Fernet
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    from sagewai.sealed import refs as refs_mod
    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.models import ProfileWritePayload

    backend = BuiltinAdminStoreBackend(profiles_path=tmp_path / "profiles.json")
    saved = await backend.save_profile(
        ProfileWritePayload(
            id="test",
            name="test",
            secrets={"A": "alpha", "B": "beta"},
        )
    )
    # Override the registered "builtin" backend with our test instance
    # for the duration of this test (monkeypatch reverts on teardown).
    monkeypatch.setitem(refs_mod._BACKENDS, "builtin", backend)

    h, v, r = await _snapshot_secret_provenance(
        f"builtin://{saved.id}", ["A", "B"],
    )
    assert h["A"] == hashlib.sha256(b"alpha").hexdigest()
    assert h["B"] == hashlib.sha256(b"beta").hexdigest()
    assert v == {"A": None, "B": None}
    assert r == {}


async def test_snapshot_provenance_swallows_backend_errors(monkeypatch):
    """Backend lookup failures degrade to empty triple (best-effort)."""
    h, v, r = await _snapshot_secret_provenance(
        "builtin://does-not-exist", ["A"],
    )
    # Lookup failed but returned cleanly with empty hashes for unknown key.
    # versions{} contains nothing because we never reached the loop.
    assert h == {}
    assert v == {}
    assert r == {}
