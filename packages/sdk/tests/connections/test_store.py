# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""ConnectionStore tests — read/write, CRUD, invariants."""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from sagewai.connections.errors import (
    ConnectionNotFoundError,
    DuplicateDisplayNameError,
    StoreCorruptedError,
    UnknownProtocolError,
    UnsupportedStoreVersionError,
)
from sagewai.connections.models import Connection
from sagewai.connections.store import ConnectionStore


# ── read/write helpers ─────────────────────────────────────────────


def test_read_empty_file_returns_default_v2_shape(tmp_path: Path):
    store = ConnectionStore(tmp_path / "connections.json")
    raw = store._read_raw()
    assert raw == {"version": 2, "connections": []}


def test_read_nonexistent_file_returns_default_v2_shape(tmp_path: Path):
    store = ConnectionStore(tmp_path / "nope.json")
    raw = store._read_raw()
    assert raw == {"version": 2, "connections": []}


def test_atomic_write_then_read_roundtrip(tmp_path: Path):
    path = tmp_path / "connections.json"
    store = ConnectionStore(path)
    store._write_raw({"version": 2, "connections": [{"id": "x"}]})
    assert path.exists()
    # File mode should be 0o600 (owner-only RW).
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    # Roundtrip
    raw = store._read_raw()
    assert raw == {"version": 2, "connections": [{"id": "x"}]}


def test_atomic_write_does_not_leave_tempfiles(tmp_path: Path):
    path = tmp_path / "connections.json"
    store = ConnectionStore(path)
    store._write_raw({"version": 2, "connections": []})
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".connections.")]
    assert leftovers == []


def test_version_1_file_raises_unsupported_version(tmp_path: Path):
    path = tmp_path / "connections.json"
    path.write_text(json.dumps({"version": 1, "providers": []}))
    store = ConnectionStore(path)
    with pytest.raises(UnsupportedStoreVersionError):
        store._read_raw()


def test_garbage_file_raises_store_corrupted(tmp_path: Path):
    path = tmp_path / "connections.json"
    path.write_text("{not valid json")
    store = ConnectionStore(path)
    with pytest.raises(StoreCorruptedError):
        store._read_raw()


def test_missing_version_key_raises_store_corrupted(tmp_path: Path):
    path = tmp_path / "connections.json"
    path.write_text(json.dumps({"connections": []}))  # no version
    store = ConnectionStore(path)
    with pytest.raises(StoreCorruptedError):
        store._read_raw()


# ── list + get ─────────────────────────────────────────────────────


def _seed_row(
    project_id: str | None = "default",
    protocol: str = "oauth2",
    display_name: str = "Test",
    tags: tuple[str, ...] = (),
    is_default: bool = False,
    protocol_data: dict | None = None,
) -> dict:
    """Hand-build a raw record dict to seed the store for tests."""
    now = "2026-05-24T00:00:00+00:00"
    return {
        "id": f"conn_{protocol}_{display_name.lower()}",
        "kind": "connection",
        "protocol": protocol,
        "project_id": project_id,
        "display_name": display_name,
        "tags": list(tags),
        "credentials_backend": None,
        "status": "pending",
        "last_tested_at": None,
        "last_test_ok": None,
        "is_default": is_default,
        "created_at": now,
        "updated_at": now,
        "last_error": None,
        "protocol_data": protocol_data or {},
    }


def test_list_returns_connections_in_project(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    store._write_raw({"version": 2, "connections": [
        _seed_row(project_id="a", display_name="A1"),
        _seed_row(project_id="b", display_name="B1"),
        _seed_row(project_id="a", display_name="A2"),
    ]})
    result = store.list(project_id="a")
    assert {c.display_name for c in result} == {"A1", "A2"}
    assert all(c.project_id == "a" for c in result)
    assert all(isinstance(c, Connection) for c in result)


def test_list_filters_by_protocol(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    store._write_raw({"version": 2, "connections": [
        _seed_row(protocol="oauth2", display_name="O1"),
        _seed_row(protocol="http", display_name="H1"),
        _seed_row(protocol="oauth2", display_name="O2"),
    ]})
    result = store.list(project_id="default", protocol="oauth2")
    assert {c.display_name for c in result} == {"O1", "O2"}


def test_list_filters_by_tag(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    store._write_raw({"version": 2, "connections": [
        _seed_row(display_name="A", tags=("music",)),
        _seed_row(display_name="B", tags=("payments",)),
        _seed_row(display_name="C", tags=("music", "production")),
    ]})
    result = store.list(project_id="default", tag="music")
    assert {c.display_name for c in result} == {"A", "C"}


def test_list_returns_empty_when_no_match(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    assert store.list(project_id="default") == []


def test_get_by_id_returns_connection(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    row = _seed_row(display_name="X")
    store._write_raw({"version": 2, "connections": [row]})
    c = store.get(row["id"])
    assert c is not None
    assert c.display_name == "X"
    assert c.tags == ()  # serialized as list, dataclass tuple


def test_get_missing_id_returns_none(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    assert store.get("nope") is None


# ── create ─────────────────────────────────────────────────────────


def test_create_generates_id_and_persists(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    c = store.create(
        protocol="oauth2",
        project_id="default",
        display_name="Spotify",
        tags=["music"],
        protocol_data={"provider": "spotify"},
    )
    assert c.id.startswith("conn_oauth2_")
    assert c.status == "pending"
    assert c.is_default is True  # first per (project, protocol) gets default
    assert c.created_at == c.updated_at
    # Persisted
    assert store.get(c.id) is not None


def test_create_returns_tags_as_tuple(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    c = store.create(
        protocol="oauth2", project_id="default", display_name="X",
        tags=["a", "b"], protocol_data={},
    )
    assert c.tags == ("a", "b")


def test_create_rejects_duplicate_display_name(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    store.create(
        protocol="oauth2", project_id="default", display_name="Same",
        tags=[], protocol_data={"provider": "spotify"},
    )
    with pytest.raises(DuplicateDisplayNameError):
        store.create(
            protocol="oauth2", project_id="default", display_name="Same",
            tags=[], protocol_data={"provider": "google"},
        )


def test_create_allows_same_display_name_in_different_project(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    store.create(
        protocol="oauth2", project_id="a", display_name="Same",
        tags=[], protocol_data={},
    )
    c = store.create(
        protocol="oauth2", project_id="b", display_name="Same",
        tags=[], protocol_data={},
    )
    assert c is not None


def test_create_allows_same_display_name_in_different_protocol(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    store.create(
        protocol="oauth2", project_id="default", display_name="Same",
        tags=[], protocol_data={},
    )
    c = store.create(
        protocol="http", project_id="default", display_name="Same",
        tags=[], protocol_data={},
    )
    assert c is not None


def test_create_rejects_unknown_protocol(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    with pytest.raises(UnknownProtocolError):
        store.create(
            protocol="not-a-protocol",
            project_id="default",
            display_name="X",
            tags=[],
            protocol_data={},
        )


def test_create_second_in_same_group_not_default(tmp_path):
    """Without a default_key_for extractor, at most one default per (project, protocol)."""
    store = ConnectionStore(tmp_path / "s.json")
    a = store.create(
        protocol="oauth2", project_id="default", display_name="A",
        tags=[], protocol_data={"provider": "spotify"},
    )
    b = store.create(
        protocol="oauth2", project_id="default", display_name="B",
        tags=[], protocol_data={"provider": "google"},
    )
    assert a.is_default is True
    assert b.is_default is False


def test_create_with_default_key_extractor(tmp_path):
    """Per-(project, protocol, provider) defaults — what PR2 will wire."""
    store = ConnectionStore(
        tmp_path / "s.json",
        default_key_for={"oauth2": lambda data: data.get("provider")},
    )
    a = store.create(
        protocol="oauth2", project_id="default", display_name="A",
        tags=[], protocol_data={"provider": "spotify"},
    )
    b = store.create(
        protocol="oauth2", project_id="default", display_name="B",
        tags=[], protocol_data={"provider": "spotify"},
    )
    c = store.create(
        protocol="oauth2", project_id="default", display_name="C",
        tags=[], protocol_data={"provider": "google"},
    )
    assert a.is_default is True
    assert b.is_default is False
    assert c.is_default is True


# ── update ─────────────────────────────────────────────────────────


def test_update_changes_display_name(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    c = store.create(
        protocol="oauth2", project_id="default", display_name="Old",
        tags=[], protocol_data={},
    )
    updated = store.update(c.id, display_name="New")
    assert updated.display_name == "New"
    assert updated.updated_at != c.updated_at
    assert updated.created_at == c.created_at


def test_update_changes_tags(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    c = store.create(
        protocol="oauth2", project_id="default", display_name="X",
        tags=["old"], protocol_data={},
    )
    updated = store.update(c.id, tags=["new", "second"])
    assert updated.tags == ("new", "second")


def test_update_changes_protocol_data(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    c = store.create(
        protocol="oauth2", project_id="default", display_name="X",
        tags=[], protocol_data={"a": 1},
    )
    updated = store.update(c.id, protocol_data={"a": 2, "b": 3})
    assert updated.protocol_data == {"a": 2, "b": 3}


def test_update_changes_credentials_backend(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    c = store.create(
        protocol="oauth2", project_id="default", display_name="X",
        tags=[], protocol_data={},
    )
    updated = store.update(c.id, credentials_backend={"kind": "env", "config": {}})
    assert updated.credentials_backend == {"kind": "env", "config": {}}


def test_update_missing_id_raises(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    with pytest.raises(ConnectionNotFoundError):
        store.update("nope", display_name="X")


def test_update_to_duplicate_display_name_raises(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    store.create(
        protocol="oauth2", project_id="default", display_name="A",
        tags=[], protocol_data={},
    )
    c = store.create(
        protocol="oauth2", project_id="default", display_name="B",
        tags=[], protocol_data={},
    )
    with pytest.raises(DuplicateDisplayNameError):
        store.update(c.id, display_name="A")


def test_update_rejects_unknown_fields(tmp_path):
    """Partial update with an unknown kwarg raises ValueError."""
    store = ConnectionStore(tmp_path / "s.json")
    c = store.create(
        protocol="oauth2", project_id="default", display_name="X",
        tags=[], protocol_data={},
    )
    with pytest.raises(ValueError):
        store.update(c.id, no_such_field="x")


# ── delete ─────────────────────────────────────────────────────────


def test_delete_removes_record(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    c = store.create(
        protocol="oauth2", project_id="default", display_name="X",
        tags=[], protocol_data={},
    )
    ok = store.delete(c.id)
    assert ok is True
    assert store.get(c.id) is None


def test_delete_missing_id_returns_false(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    assert store.delete("nope") is False


# ── set_default ────────────────────────────────────────────────────


def test_set_default_marks_target(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    a = store.create(
        protocol="oauth2", project_id="default", display_name="A",
        tags=[], protocol_data={},
    )
    b = store.create(
        protocol="oauth2", project_id="default", display_name="B",
        tags=[], protocol_data={},
    )
    updated = store.set_default(b.id)
    assert updated.is_default is True
    a2 = store.get(a.id)
    assert a2 is not None
    assert a2.is_default is False


def test_set_default_uses_default_key_extractor(tmp_path):
    store = ConnectionStore(
        tmp_path / "s.json",
        default_key_for={"oauth2": lambda data: data.get("provider")},
    )
    s1 = store.create(
        protocol="oauth2", project_id="default", display_name="S1",
        tags=[], protocol_data={"provider": "spotify"},
    )
    s2 = store.create(
        protocol="oauth2", project_id="default", display_name="S2",
        tags=[], protocol_data={"provider": "spotify"},
    )
    g1 = store.create(
        protocol="oauth2", project_id="default", display_name="G1",
        tags=[], protocol_data={"provider": "google"},
    )
    assert store.get(s1.id).is_default is True
    assert store.get(s2.id).is_default is False
    assert store.get(g1.id).is_default is True
    store.set_default(s2.id)
    assert store.get(s1.id).is_default is False
    assert store.get(s2.id).is_default is True
    assert store.get(g1.id).is_default is True


def test_set_default_missing_id_raises(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    with pytest.raises(ConnectionNotFoundError):
        store.set_default("nope")


# ── update_test_result ─────────────────────────────────────────────


def test_update_test_result_records_ok(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    c = store.create(
        protocol="oauth2", project_id="default", display_name="X",
        tags=[], protocol_data={},
    )
    updated = store.update_test_result(c.id, ok=True)
    assert updated.last_test_ok is True
    assert updated.last_tested_at is not None


def test_update_test_result_records_failure(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    c = store.create(
        protocol="oauth2", project_id="default", display_name="X",
        tags=[], protocol_data={},
    )
    updated = store.update_test_result(c.id, ok=False)
    assert updated.last_test_ok is False
    assert updated.last_tested_at is not None


def test_update_test_result_missing_id_raises(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    with pytest.raises(ConnectionNotFoundError):
        store.update_test_result("nope", ok=True)
