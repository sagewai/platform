"""Tests for PostgresGuardrailStore.

Requires PostgreSQL: SAGEWAI_DATABASE_URL env var.
"""

import os

import pytest
import pytest_asyncio

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("SAGEWAI_DATABASE_URL"),
        reason="SAGEWAI_DATABASE_URL not set",
    ),
]


@pytest_asyncio.fixture
async def store():
    from sagewai.admin.postgres_guardrails import PostgresGuardrailStore

    s = PostgresGuardrailStore(os.environ["SAGEWAI_DATABASE_URL"])
    await s.init()
    await s.clear()
    yield s
    await s.clear()
    await s.close()


class TestGuardrailConfigCRUD:
    async def test_upsert_and_list(self, store):
        result = await store.upsert_config(
            agent_name="agent-a",
            guardrail_type="pii",
            enabled=True,
            config={"action": "redact"},
        )
        assert result["agent_name"] == "agent-a"
        assert result["guardrail_type"] == "pii"
        assert result["enabled"] is True
        assert result["config"] == {"action": "redact"}

        configs = await store.list_configs()
        assert len(configs) == 1

    async def test_upsert_updates_existing(self, store):
        await store.upsert_config("agent-a", "pii", enabled=True)
        await store.upsert_config("agent-a", "pii", enabled=False)

        configs = await store.list_configs(agent_name="agent-a")
        assert len(configs) == 1
        assert configs[0]["enabled"] is False

    async def test_get_config(self, store):
        await store.upsert_config("agent-b", "hallucination", config={"threshold": 0.7})
        result = await store.get_config("agent-b", "hallucination")
        assert result is not None
        assert result["config"] == {"threshold": 0.7}

    async def test_get_config_not_found(self, store):
        result = await store.get_config("nonexistent", "pii")
        assert result is None

    async def test_delete_config(self, store):
        await store.upsert_config("agent-c", "content_filter")
        deleted = await store.delete_config("agent-c", "content_filter")
        assert deleted is True

        deleted_again = await store.delete_config("agent-c", "content_filter")
        assert deleted_again is False

    async def test_delete_all_configs(self, store):
        await store.upsert_config("agent-d", "pii")
        await store.upsert_config("agent-d", "hallucination")
        await store.upsert_config("agent-d", "content_filter")

        count = await store.delete_all_configs("agent-d")
        assert count == 3

    async def test_list_filtered_by_agent(self, store):
        await store.upsert_config("agent-a", "pii")
        await store.upsert_config("agent-b", "hallucination")

        configs_a = await store.list_configs(agent_name="agent-a")
        assert len(configs_a) == 1
        assert configs_a[0]["agent_name"] == "agent-a"

    async def test_multiple_guardrail_types(self, store):
        await store.upsert_config("agent-a", "pii", enabled=True)
        await store.upsert_config("agent-a", "hallucination", enabled=False)
        await store.upsert_config("agent-a", "content_filter", enabled=True)

        configs = await store.list_configs(agent_name="agent-a")
        assert len(configs) == 3
        types = {c["guardrail_type"] for c in configs}
        assert types == {"pii", "hallucination", "content_filter"}


class TestAuditLog:
    async def test_list_events(self, store):
        # Record events via the analytics store's guardrail_events table
        import asyncpg

        conn = await asyncpg.connect(os.environ["SAGEWAI_DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM guardrail_events")
            await conn.execute(
                "INSERT INTO guardrail_events (agent_name, event_type, detail)"
                " VALUES ($1, $2, $3)",
                "agent-a",
                "pii_detected",
                "EMAIL found",
            )
            await conn.execute(
                "INSERT INTO guardrail_events (agent_name, event_type, detail)"
                " VALUES ($1, $2, $3)",
                "agent-a",
                "hallucination",
                "Low confidence",
            )
        finally:
            await conn.close()

        events = await store.list_events()
        assert len(events) == 2

    async def test_list_events_filtered(self, store):
        import asyncpg

        conn = await asyncpg.connect(os.environ["SAGEWAI_DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM guardrail_events")
            await conn.execute(
                "INSERT INTO guardrail_events (agent_name, event_type, detail)"
                " VALUES ($1, $2, $3)",
                "agent-a",
                "pii_detected",
                "EMAIL found",
            )
            await conn.execute(
                "INSERT INTO guardrail_events (agent_name, event_type, detail)"
                " VALUES ($1, $2, $3)",
                "agent-b",
                "hallucination",
                "Low score",
            )
        finally:
            await conn.close()

        events = await store.list_events(agent_name="agent-a")
        assert len(events) == 1
        assert events[0]["event_type"] == "pii_detected"

    async def test_count_events(self, store):
        import asyncpg

        conn = await asyncpg.connect(os.environ["SAGEWAI_DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM guardrail_events")
            for i in range(5):
                await conn.execute(
                    "INSERT INTO guardrail_events (agent_name, event_type)"
                    " VALUES ($1, $2)",
                    "agent-a",
                    "pii_detected",
                )
        finally:
            await conn.close()

        count = await store.count_events(event_type="pii_detected")
        assert count == 5

    async def test_export_events(self, store):
        import asyncpg

        conn = await asyncpg.connect(os.environ["SAGEWAI_DATABASE_URL"])
        try:
            await conn.execute("DELETE FROM guardrail_events")
            await conn.execute(
                "INSERT INTO guardrail_events (agent_name, event_type)"
                " VALUES ($1, $2)",
                "agent-a",
                "content_filter",
            )
        finally:
            await conn.close()

        events = await store.export_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "content_filter"
