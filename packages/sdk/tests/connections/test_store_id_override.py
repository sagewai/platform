# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for ConnectionStore.create(id_override=...) extension."""
from __future__ import annotations

import pytest

from sagewai.connections.errors import IdCollisionError
from sagewai.connections.store import ConnectionStore


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(
        tmp_path / "connections.json",
        allowed_protocols=("http",),
    )


def test_create_without_id_override_generates_id(store):
    conn = store.create(
        protocol="http",
        project_id="proj-test",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
    )
    assert conn.id  # non-empty
    assert conn.id != "explicit-id"


def test_create_with_id_override_honors_id(store):
    conn = store.create(
        protocol="http",
        project_id="proj-test",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        id_override="conn-custom-001",
    )
    assert conn.id == "conn-custom-001"


def test_create_with_colliding_id_override_raises(store):
    store.create(
        protocol="http",
        project_id="proj-test",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        id_override="conn-custom-001",
    )
    # Same id, different display_name → still collides.
    with pytest.raises(IdCollisionError) as exc_info:
        store.create(
            protocol="http",
            project_id="proj-test",
            display_name="beta",
            tags=[],
            protocol_data={"base_url": "https://b.com", "auth": {"kind": "none"}},
            id_override="conn-custom-001",
        )
    assert "conn-custom-001" in str(exc_info.value)


def test_create_id_override_persists(store, tmp_path):
    store.create(
        protocol="http",
        project_id="proj-test",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        id_override="conn-custom-001",
    )
    # Reload the store from disk; the id_override should round-trip.
    store2 = ConnectionStore(
        tmp_path / "connections.json",
        allowed_protocols=("http",),
    )
    conns = list(store2.list(project_id="proj-test"))
    assert len(conns) == 1
    assert conns[0].id == "conn-custom-001"
