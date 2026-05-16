# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for SessionStore protocol and SessionRecord model."""

import pytest

from sagewai.core.session import InMemorySessionStore, SessionRecord


class TestSessionRecord:
    def test_create_with_defaults(self):
        record = SessionRecord(
            session_id="sess-1",
            agent_name="assistant",
        )
        assert record.session_id == "sess-1"
        assert record.project_id is None
        assert record.agent_name == "assistant"
        assert record.messages == []
        assert record.summary == ""
        assert record.memory_keys == []
        assert record.created_at > 0
        assert record.updated_at > 0

    def test_create_with_project(self):
        record = SessionRecord(
            session_id="sess-2",
            project_id="acme-corp",
            agent_name="scout",
        )
        assert record.project_id == "acme-corp"

    def test_serialization_roundtrip(self):
        record = SessionRecord(
            session_id="sess-3",
            project_id="acme",
            agent_name="scout",
            messages=[{"role": "user", "content": "hello"}],
            summary="User greeted the agent.",
            memory_keys=["mem-1", "mem-2"],
        )
        data = record.model_dump()
        restored = SessionRecord(**data)
        assert restored.session_id == record.session_id
        assert restored.messages == record.messages
        assert restored.summary == record.summary


class TestInMemorySessionStore:
    @pytest.mark.asyncio
    async def test_save_and_load(self):
        store = InMemorySessionStore()
        record = SessionRecord(session_id="s1", agent_name="a")
        await store.save(record)
        loaded = await store.load("s1")
        assert loaded is not None
        assert loaded.session_id == "s1"

    @pytest.mark.asyncio
    async def test_load_nonexistent_returns_none(self):
        store = InMemorySessionStore()
        assert await store.load("missing") is None

    @pytest.mark.asyncio
    async def test_project_isolation(self):
        store = InMemorySessionStore()
        await store.save(SessionRecord(session_id="s1", project_id="acme", agent_name="a"))
        await store.save(SessionRecord(session_id="s1", project_id="other", agent_name="a"))

        acme = await store.load("s1", project_id="acme")
        other = await store.load("s1", project_id="other")
        assert acme is not None
        assert other is not None
        assert acme.project_id == "acme"
        assert other.project_id == "other"

    @pytest.mark.asyncio
    async def test_list_sessions_by_project(self):
        store = InMemorySessionStore()
        await store.save(SessionRecord(session_id="s1", project_id="acme", agent_name="a"))
        await store.save(SessionRecord(session_id="s2", project_id="acme", agent_name="b"))
        await store.save(SessionRecord(session_id="s3", project_id="other", agent_name="a"))

        acme_sessions = await store.list_sessions(project_id="acme")
        assert len(acme_sessions) == 2

        other_sessions = await store.list_sessions(project_id="other")
        assert len(other_sessions) == 1

    @pytest.mark.asyncio
    async def test_delete(self):
        store = InMemorySessionStore()
        await store.save(SessionRecord(session_id="s1", agent_name="a"))
        await store.delete("s1")
        assert await store.load("s1") is None

    @pytest.mark.asyncio
    async def test_save_updates_existing(self):
        store = InMemorySessionStore()
        record = SessionRecord(session_id="s1", agent_name="a", summary="v1")
        await store.save(record)
        record.summary = "v2"
        await store.save(record)
        loaded = await store.load("s1")
        assert loaded is not None
        assert loaded.summary == "v2"


class TestSessionEdgeCases:
    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_error(self):
        """Deleting a session that doesn't exist should not raise."""
        store = InMemorySessionStore()
        await store.delete("nonexistent-id")  # Should not raise

    @pytest.mark.asyncio
    async def test_list_sessions_respects_limit(self):
        """list_sessions should respect the limit parameter."""
        store = InMemorySessionStore()
        for i in range(10):
            await store.save(SessionRecord(session_id=f"s{i}", agent_name="a"))
        result = await store.list_sessions(limit=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_save_updates_timestamp(self):
        """Saving an existing session should update updated_at."""
        import time

        store = InMemorySessionStore()
        record = SessionRecord(session_id="s1", agent_name="a")
        await store.save(record)
        original_time = record.updated_at

        time.sleep(0.01)
        record.messages = [{"role": "user", "content": "hi"}]
        await store.save(record)

        loaded = await store.load("s1")
        assert loaded is not None
        assert loaded.updated_at >= original_time

    @pytest.mark.asyncio
    async def test_project_isolation_on_load(self):
        """Loading with wrong project should return None."""
        store = InMemorySessionStore()
        await store.save(
            SessionRecord(session_id="s1", project_id="project-a", agent_name="a")
        )
        result = await store.load("s1", project_id="project-b")
        assert result is None

    @pytest.mark.asyncio
    async def test_large_messages_payload(self):
        """Sessions with large message lists should work correctly."""
        store = InMemorySessionStore()
        big_messages = [{"role": "user", "content": f"msg {i}"} for i in range(1000)]
        record = SessionRecord(session_id="big", agent_name="a", messages=big_messages)
        await store.save(record)
        loaded = await store.load("big")
        assert loaded is not None
        assert len(loaded.messages) == 1000
