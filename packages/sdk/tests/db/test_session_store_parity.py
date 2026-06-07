# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for PostgresSessionStore — runs against both SQLite and Postgres."""
import time

import pytest

from sagewai.core.session import SessionRecord
from sagewai.core.stores.session_store import PostgresSessionStore


def _rec(sid="s1", pid="p1", msgs=None):
    now = time.time()
    return SessionRecord(
        session_id=sid, project_id=pid, agent_name="a",
        messages=msgs or [{"role": "user", "content": "hi"}],
        summary="sum", memory_keys=["k1"], created_at=now, updated_at=now,
    )


@pytest.mark.asyncio
async def test_save_and_load(dialect_engine):
    store = PostgresSessionStore(engine=dialect_engine)
    await store.save(_rec())
    loaded = await store.load("s1", "p1")
    assert loaded is not None
    assert loaded.session_id == "s1" and loaded.agent_name == "a"
    assert loaded.messages == [{"role": "user", "content": "hi"}]
    assert loaded.memory_keys == ["k1"]


@pytest.mark.asyncio
async def test_upsert_updates_messages(dialect_engine):
    store = PostgresSessionStore(engine=dialect_engine)
    await store.save(_rec())
    await store.save(_rec(msgs=[{"role": "user", "content": "updated"}]))
    loaded = await store.load("s1", "p1")
    assert loaded.messages == [{"role": "user", "content": "updated"}]


@pytest.mark.asyncio
async def test_list_sessions_by_project(dialect_engine):
    store = PostgresSessionStore(engine=dialect_engine)
    await store.save(_rec(sid="s1", pid="p1"))
    await store.save(_rec(sid="s2", pid="p1"))
    await store.save(_rec(sid="s3", pid="p2"))
    p1 = await store.list_sessions("p1")
    assert {s.session_id for s in p1} == {"s1", "s2"}


@pytest.mark.asyncio
async def test_delete(dialect_engine):
    store = PostgresSessionStore(engine=dialect_engine)
    await store.save(_rec())
    await store.delete("s1", "p1")
    assert await store.load("s1", "p1") is None


@pytest.mark.asyncio
async def test_load_missing_returns_none(dialect_engine):
    store = PostgresSessionStore(engine=dialect_engine)
    assert await store.load("nope", "p1") is None


def test_row_to_record_decodes_json_strings():
    """_row_to_record must handle JSON strings for messages/memory_keys (legacy/asyncpg rows)."""
    row = {
        "session_id": "s1", "project_id": "p1", "agent_name": "a",
        "messages": '[{"role": "user", "content": "hi"}]',  # JSON string, not list
        "summary": "s", "memory_keys": '["k1"]',
        "created_at": 1000.0, "updated_at": 1000.0,
    }
    rec = PostgresSessionStore._row_to_record(row)
    assert rec.messages == [{"role": "user", "content": "hi"}]
    assert rec.memory_keys == ["k1"]


@pytest.mark.asyncio
async def test_project_id_none_roundtrips(dialect_engine):
    """project_id=None must survive a save/load round-trip unchanged."""
    store = PostgresSessionStore(engine=dialect_engine)
    rec = _rec(sid="snone", pid=None)
    await store.save(rec)
    loaded = await store.load("snone", None)
    assert loaded is not None
    assert loaded.project_id is None
