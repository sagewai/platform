# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for PostgresSessionStore.

These tests use mocking — no real database required.
For integration tests, run with a live PostgreSQL instance.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.core.session import SessionRecord
from sagewai.core.stores.session_store import PostgresSessionStore


class TestPostgresSessionStore:
    @pytest.mark.asyncio
    async def test_save_executes_upsert(self):
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        store = PostgresSessionStore(pool=mock_pool)

        record = SessionRecord(
            session_id="s1",
            project_id="acme",
            agent_name="assistant",
            messages=[{"role": "user", "content": "hi"}],
            summary="User said hi.",
        )
        await store.save(record)
        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args
        assert "INSERT INTO sessions" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_load_returns_record(self):
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(
            return_value={
                "session_id": "s1",
                "project_id": "acme",
                "agent_name": "assistant",
                "messages": '[{"role": "user", "content": "hi"}]',
                "summary": "User said hi.",
                "memory_keys": "[]",
                "created_at": 1000.0,
                "updated_at": 1000.0,
            }
        )
        store = PostgresSessionStore(pool=mock_pool)

        record = await store.load("s1", project_id="acme")
        assert record is not None
        assert record.session_id == "s1"
        assert record.project_id == "acme"

    @pytest.mark.asyncio
    async def test_load_returns_none_when_not_found(self):
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        store = PostgresSessionStore(pool=mock_pool)

        assert await store.load("missing") is None

    @pytest.mark.asyncio
    async def test_delete_executes_query(self):
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        store = PostgresSessionStore(pool=mock_pool)

        await store.delete("s1", project_id="acme")
        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args
        assert "DELETE FROM sessions" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_list_sessions_queries_by_project(self):
        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        store = PostgresSessionStore(pool=mock_pool)

        result = await store.list_sessions(project_id="acme", limit=10)
        assert result == []
        mock_pool.fetch.assert_called_once()
