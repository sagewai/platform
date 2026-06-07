# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for PostgresAgentStore — runs against both SQLite and Postgres.

Covers:
- upsert (conflict-update on name PK)
- get / list / delete
- spec JSON round-trip (nested objects, lists, unicode)
- rename (atomic delete-old / insert-new)
- migration-faithful upsert test (raw DDL → ON CONFLICT (name) must work)

Uses the ``dialect_engine`` fixture from tests/db/conftest.py
(SQLite always; Postgres when SAGEWAI_TEST_DATABASE_URL is set).
"""
from __future__ import annotations

import pytest

from sagewai.admin.agent_store import PostgresAgentStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASIC_SPEC: dict = {
    "name": "scout",
    "model": "claude-opus-4-5",
    "system_prompt": "You are a scout.",
    "tools": ["web_search"],
    "temperature": 0.7,
}

_TRANSIENT_SPEC: dict = {
    **_BASIC_SPEC,
    "api_key": "sk-secret",
    "api_base": "https://api.example.com",
    "custom_llm_provider": "anthropic",
}


# ---------------------------------------------------------------------------
# Basic save / get / list / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_list_all(dialect_engine):
    store = PostgresAgentStore(engine=dialect_engine)
    await store.init()

    await store.save("scout", _BASIC_SPEC)
    agents = await store.list_all()
    assert len(agents) == 1
    assert agents[0]["name"] == "scout"
    assert agents[0]["model"] == "claude-opus-4-5"


@pytest.mark.asyncio
async def test_save_strips_transient_fields(dialect_engine):
    """Transient provider fields must be stripped before persisting."""
    store = PostgresAgentStore(engine=dialect_engine)
    await store.init()

    await store.save("scout", _TRANSIENT_SPEC)
    agents = await store.list_all()
    assert len(agents) == 1
    rec = agents[0]
    assert "api_key" not in rec
    assert "api_base" not in rec
    assert "custom_llm_provider" not in rec
    # non-transient field still present
    assert rec["model"] == "claude-opus-4-5"


@pytest.mark.asyncio
async def test_conflict_update_on_name(dialect_engine):
    """Second save on the same name must update, not raise."""
    store = PostgresAgentStore(engine=dialect_engine)
    await store.init()

    await store.save("scout", _BASIC_SPEC)
    updated = {**_BASIC_SPEC, "model": "claude-haiku-4-5"}
    await store.save("scout", updated)

    agents = await store.list_all()
    assert len(agents) == 1
    assert agents[0]["model"] == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_list_all_ordered_by_created_at(dialect_engine):
    """list_all returns agents ordered by created_at (ascending)."""
    store = PostgresAgentStore(engine=dialect_engine)
    await store.init()

    for n in ["alpha", "beta", "gamma"]:
        await store.save(n, {**_BASIC_SPEC, "name": n})

    agents = await store.list_all()
    assert len(agents) == 3
    names = [a["name"] for a in agents]
    # Order must be creation order (alpha first)
    assert names == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_delete_existing(dialect_engine):
    store = PostgresAgentStore(engine=dialect_engine)
    await store.init()

    await store.save("scout", _BASIC_SPEC)
    deleted = await store.delete("scout")
    assert deleted is True
    assert await store.list_all() == []


@pytest.mark.asyncio
async def test_delete_missing(dialect_engine):
    store = PostgresAgentStore(engine=dialect_engine)
    await store.init()

    deleted = await store.delete("nonexistent")
    assert deleted is False


# ---------------------------------------------------------------------------
# JSON spec round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spec_json_roundtrip(dialect_engine):
    """Nested dicts, lists, unicode, and numeric types survive the round-trip."""
    store = PostgresAgentStore(engine=dialect_engine)
    await store.init()

    complex_spec = {
        "name": "complex",
        "model": "claude-opus-4-5",
        "tools": ["web_search", "read_rss"],
        "metadata": {"region": "eu-west-1", "tier": 2},
        "unicode": "Sagewai — AI élève",
    }
    await store.save("complex", complex_spec)
    agents = await store.list_all()
    assert len(agents) == 1
    assert agents[0] == complex_spec


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_existing(dialect_engine):
    store = PostgresAgentStore(engine=dialect_engine)
    await store.init()

    await store.save("old-name", {**_BASIC_SPEC, "name": "old-name"})
    result = await store.rename("old-name", "new-name")
    assert result is True

    agents = await store.list_all()
    names = [a["name"] for a in agents]
    assert "new-name" in names
    assert "old-name" not in names
    # spec name field is also updated
    assert agents[0]["name"] == "new-name"


@pytest.mark.asyncio
async def test_rename_missing(dialect_engine):
    store = PostgresAgentStore(engine=dialect_engine)
    await store.init()

    result = await store.rename("ghost", "new-name")
    assert result is False


# ---------------------------------------------------------------------------
# Multiple agents — list isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_agents(dialect_engine):
    store = PostgresAgentStore(engine=dialect_engine)
    await store.init()

    await store.save("scout", {**_BASIC_SPEC, "name": "scout"})
    await store.save("writer", {**_BASIC_SPEC, "name": "writer", "model": "claude-haiku-4-5"})

    all_agents = await store.list_all()
    assert len(all_agents) == 2
    models = {a["name"]: a["model"] for a in all_agents}
    assert models["scout"] == "claude-opus-4-5"
    assert models["writer"] == "claude-haiku-4-5"

    await store.delete("scout")
    remaining = await store.list_all()
    assert len(remaining) == 1
    assert remaining[0]["name"] == "writer"


# ---------------------------------------------------------------------------
# Migration-faithful upsert test — raw DDL, then ON CONFLICT (name)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_against_migration_schema():
    """Prove ON CONFLICT (name) works on a table built exactly as migration 001.

    The ``dialect_engine`` fixture calls ``Base.metadata.create_all()``, which
    on PostgreSQL would create the model-defined schema.  This test bypasses
    ``create_all`` entirely and reconstructs ``playground_agents`` with only the
    constraints that migration 001 actually creates on a production database:

    * ``name`` VARCHAR(255) PRIMARY KEY
    * non-unique index on ``project_id``
    * NO extra unique constraints

    Saving the same agent name twice must update the row (upsert), not raise
    "no unique or exclusion constraint matching the ON CONFLICT specification".
    """
    import os

    import pytest

    from sqlalchemy import func, select, text

    from sagewai.db.engine import create_engine

    pg_url = os.environ.get("SAGEWAI_TEST_DATABASE_URL")
    if not pg_url:
        pytest.skip("SAGEWAI_TEST_DATABASE_URL not set — Postgres-only test")

    engine = create_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS playground_agents CASCADE"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE playground_agents (
                        name        VARCHAR(255) PRIMARY KEY,
                        project_id  TEXT NOT NULL DEFAULT 'default',
                        spec        TEXT NOT NULL,
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX ix_playground_agents_project_id"
                    " ON playground_agents (project_id)"
                )
            )

        store = PostgresAgentStore(engine=engine)
        spec = {
            "name": "migration-faithful-agent",
            "model": "claude-opus-4-5",
            "system_prompt": "First version.",
        }
        await store.save("migration-faithful-agent", spec)
        spec["system_prompt"] = "Second version."
        # This second save must NOT raise — ON CONFLICT (name) targets the PK.
        await store.save("migration-faithful-agent", spec)

        agents = await store.list_all()
        assert len(agents) == 1
        assert agents[0]["system_prompt"] == "Second version.", (
            "upsert should have updated system_prompt to 'Second version.'"
        )

        # Confirm exactly one row
        from sagewai.db.models import PlaygroundAgentModel

        tbl = PlaygroundAgentModel.__table__
        async with engine.connect() as conn:
            count = (
                await conn.execute(select(func.count()).select_from(tbl))
            ).scalar_one()
        assert count == 1, f"expected 1 row after upsert, got {count}"
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS playground_agents CASCADE"))
        await engine.dispose()
