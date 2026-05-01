# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for InstanceIdentity and its storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from sagewai.autopilot.sagewai_llm.identity import (
    FileIdentityStore,
    InstanceIdentity,
    InstanceIdentityStore,
    ensure_identity,
)


def test_identity_has_uuid_id_and_32_byte_secret():
    ident = InstanceIdentity.generate()
    assert len(ident.instance_id) == 32  # 16-byte uuid hex
    assert len(ident.instance_secret) == 64  # 32 bytes hex


def test_two_generated_identities_are_distinct():
    a = InstanceIdentity.generate()
    b = InstanceIdentity.generate()
    assert a.instance_id != b.instance_id
    assert a.instance_secret != b.instance_secret


def test_file_store_round_trip(tmp_path: Path):
    store = FileIdentityStore(tmp_path / "identity.json")
    assert store.load() is None  # empty
    new = InstanceIdentity.generate()
    store.save(new)
    loaded = store.load()
    assert loaded is not None
    assert loaded.instance_id == new.instance_id
    assert loaded.instance_secret == new.instance_secret


def test_file_store_persists_across_instances(tmp_path: Path):
    path = tmp_path / "identity.json"
    new = InstanceIdentity.generate()
    FileIdentityStore(path).save(new)
    reloaded = FileIdentityStore(path).load()
    assert reloaded == new


def test_file_store_creates_parent_directories(tmp_path: Path):
    nested = tmp_path / "a" / "b" / "c" / "identity.json"
    store = FileIdentityStore(nested)
    store.save(InstanceIdentity.generate())
    assert nested.exists()


def test_ensure_identity_generates_when_missing(tmp_path: Path):
    store = FileIdentityStore(tmp_path / "identity.json")
    ident = ensure_identity(store)
    assert store.load() == ident  # persisted


def test_ensure_identity_returns_existing_when_present(tmp_path: Path):
    store = FileIdentityStore(tmp_path / "identity.json")
    first = ensure_identity(store)
    second = ensure_identity(store)
    assert first == second  # stable across calls


def test_identity_store_protocol_is_runtime_checkable():
    class InMemoryStore:
        def __init__(self) -> None:
            self._value: InstanceIdentity | None = None

        def load(self) -> InstanceIdentity | None:
            return self._value

        def save(self, ident: InstanceIdentity) -> None:
            self._value = ident

    assert isinstance(InMemoryStore(), InstanceIdentityStore)


def test_identity_is_frozen():
    ident = InstanceIdentity.generate()
    with pytest.raises(Exception):
        ident.instance_id = "other"  # type: ignore[misc]
