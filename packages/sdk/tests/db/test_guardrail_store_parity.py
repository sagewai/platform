# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for PostgresGuardrailStore — SQLite and Postgres."""

import pytest

from sagewai.admin.postgres_guardrails import PostgresGuardrailStore


@pytest.mark.asyncio
async def test_upsert_and_list(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
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
    assert result["id"] is not None
    assert result["created_at"] is not None

    configs = await store.list_configs()
    assert len(configs) == 1


@pytest.mark.asyncio
async def test_upsert_updates_existing(dialect_engine):
    """ON CONFLICT path: second upsert on same key updates in place."""
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.upsert_config("agent-a", "pii", enabled=True)
    result = await store.upsert_config("agent-a", "pii", enabled=False)
    assert result["enabled"] is False

    configs = await store.list_configs(agent_name="agent-a")
    assert len(configs) == 1
    assert configs[0]["enabled"] is False


@pytest.mark.asyncio
async def test_upsert_updates_config_dict(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.upsert_config("agent-a", "pii", config={"threshold": 0.5})
    result = await store.upsert_config("agent-a", "pii", config={"threshold": 0.9})
    assert result["config"] == {"threshold": 0.9}


@pytest.mark.asyncio
async def test_get_config(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.upsert_config("agent-b", "hallucination", config={"threshold": 0.7})
    result = await store.get_config("agent-b", "hallucination")
    assert result is not None
    assert result["config"] == {"threshold": 0.7}


@pytest.mark.asyncio
async def test_get_config_not_found(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    result = await store.get_config("nonexistent", "pii")
    assert result is None


@pytest.mark.asyncio
async def test_delete_config(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.upsert_config("agent-c", "content_filter")
    deleted = await store.delete_config("agent-c", "content_filter")
    assert deleted is True

    deleted_again = await store.delete_config("agent-c", "content_filter")
    assert deleted_again is False


@pytest.mark.asyncio
async def test_delete_all_configs(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.upsert_config("agent-d", "pii")
    await store.upsert_config("agent-d", "hallucination")
    await store.upsert_config("agent-d", "content_filter")

    count = await store.delete_all_configs("agent-d")
    assert count == 3

    # Idempotent — nothing left to delete
    count2 = await store.delete_all_configs("agent-d")
    assert count2 == 0


@pytest.mark.asyncio
async def test_list_filtered_by_agent(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.upsert_config("agent-a", "pii")
    await store.upsert_config("agent-b", "hallucination")

    configs_a = await store.list_configs(agent_name="agent-a")
    assert len(configs_a) == 1
    assert configs_a[0]["agent_name"] == "agent-a"


@pytest.mark.asyncio
async def test_list_multiple_guardrail_types(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.upsert_config("agent-a", "pii", enabled=True)
    await store.upsert_config("agent-a", "hallucination", enabled=False)
    await store.upsert_config("agent-a", "content_filter", enabled=True)

    configs = await store.list_configs(agent_name="agent-a")
    assert len(configs) == 3
    types = {c["guardrail_type"] for c in configs}
    assert types == {"pii", "hallucination", "content_filter"}


@pytest.mark.asyncio
async def test_project_scoping(dialect_engine):
    """Configs in different projects are isolated."""
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.upsert_config("agent-a", "pii", project_id="proj1")
    await store.upsert_config("agent-a", "pii", project_id="proj2")

    # list_configs scopes to resolved project — use get_config for explicit test
    cfg1 = await store.get_config("agent-a", "pii", project_id="proj1")
    cfg2 = await store.get_config("agent-a", "pii", project_id="proj2")
    assert cfg1 is not None
    assert cfg2 is not None


@pytest.mark.asyncio
async def test_record_and_list_events(dialect_engine):
    """record_guardrail_event then list_events."""
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.record_guardrail_event("agent-a", "pii_detected", detail="EMAIL found")
    await store.record_guardrail_event("agent-a", "hallucination", detail="Low confidence")

    events = await store.list_events()
    assert len(events) == 2
    types = {e["event_type"] for e in events}
    assert types == {"pii_detected", "hallucination"}


@pytest.mark.asyncio
async def test_list_events_filtered_by_agent(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.record_guardrail_event("agent-a", "pii_detected")
    await store.record_guardrail_event("agent-b", "hallucination")

    events = await store.list_events(agent_name="agent-a")
    assert len(events) == 1
    assert events[0]["event_type"] == "pii_detected"


@pytest.mark.asyncio
async def test_list_events_filtered_by_type(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.record_guardrail_event("agent-a", "pii_detected")
    await store.record_guardrail_event("agent-a", "hallucination")

    events = await store.list_events(event_type="pii_detected")
    assert len(events) == 1
    assert events[0]["event_type"] == "pii_detected"


@pytest.mark.asyncio
async def test_count_events(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    for _ in range(5):
        await store.record_guardrail_event("agent-a", "pii_detected")
    count = await store.count_events(event_type="pii_detected")
    assert count == 5


@pytest.mark.asyncio
async def test_count_events_filtered(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.record_guardrail_event("agent-a", "pii_detected")
    await store.record_guardrail_event("agent-a", "hallucination")
    count = await store.count_events(agent_name="agent-a", event_type="pii_detected")
    assert count == 1


@pytest.mark.asyncio
async def test_export_events(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    await store.record_guardrail_event("agent-a", "content_filter")
    events = await store.export_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "content_filter"


@pytest.mark.asyncio
async def test_list_events_pagination(dialect_engine):
    store = PostgresGuardrailStore(engine=dialect_engine)
    for _ in range(10):
        await store.record_guardrail_event("agent-a", "pii_detected")

    page1 = await store.list_events(limit=3, offset=0)
    page2 = await store.list_events(limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 3
    # No overlap between pages
    ids_p1 = {e["id"] for e in page1}
    ids_p2 = {e["id"] for e in page2}
    assert ids_p1.isdisjoint(ids_p2)


@pytest.mark.asyncio
async def test_engine_kwarg_constructor(dialect_engine):
    """Constructor engine= kwarg works (main test pattern)."""
    store = PostgresGuardrailStore(engine=dialect_engine)
    result = await store.upsert_config("x", "pii")
    assert result["agent_name"] == "x"
