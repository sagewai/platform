# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for PostgresSessionStore (SQLAlchemy Core backend).

Uses an in-memory SQLite engine so no real database is required.
"""

import pytest
import pytest_asyncio

from sagewai.core.session import SessionRecord
from sagewai.core.stores.session_store import PostgresSessionStore
from sagewai.db.engine import create_engine
from sagewai.db.models import Base


@pytest_asyncio.fixture
async def store(tmp_path):
    """In-memory SQLite engine with schema bootstrapped."""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    s = PostgresSessionStore(engine=engine)
    yield s
    await engine.dispose()


def _record(sid="s1", pid="acme"):
    return SessionRecord(
        session_id=sid,
        project_id=pid,
        agent_name="assistant",
        messages=[{"role": "user", "content": "hi"}],
        summary="User said hi.",
    )


class TestPostgresSessionStore:
    @pytest.mark.asyncio
    async def test_save_executes_upsert(self, store):
        record = _record()
        await store.save(record)
        loaded = await store.load("s1", project_id="acme")
        assert loaded is not None
        assert loaded.session_id == "s1"

    @pytest.mark.asyncio
    async def test_load_returns_record(self, store):
        await store.save(_record())
        record = await store.load("s1", project_id="acme")
        assert record is not None
        assert record.session_id == "s1"
        assert record.project_id == "acme"

    @pytest.mark.asyncio
    async def test_load_returns_none_when_not_found(self, store):
        assert await store.load("missing") is None

    @pytest.mark.asyncio
    async def test_delete_executes_query(self, store):
        await store.save(_record())
        await store.delete("s1", project_id="acme")
        assert await store.load("s1", project_id="acme") is None

    @pytest.mark.asyncio
    async def test_list_sessions_queries_by_project(self, store):
        await store.save(_record(sid="s1", pid="acme"))
        await store.save(_record(sid="s2", pid="other"))
        result = await store.list_sessions(project_id="acme", limit=10)
        assert len(result) == 1
        assert result[0].session_id == "s1"
