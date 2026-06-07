# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for the 2 gateway stores — runs against both SQLite and Postgres.

Covers: PostgresTokenStore, PostgresTriggerStore.
Uses the dialect_engine fixture from tests/db/conftest.py (SQLite always;
Postgres when SAGEWAI_TEST_DATABASE_URL is set).
"""
from __future__ import annotations

import time
from datetime import timedelta

import pytest

from sagewai.gateway.models import AccessToken, TokenStatus
from sagewai.gateway.postgres_store import PostgresTokenStore
from sagewai.gateway.pg_trigger_store import PostgresTriggerStore
from sagewai.gateway.triggers import EventFilter, Strategy, TriggerSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    token_id: str = "tok-001",
    token_hash: str = "hash-aaa",
    agent_name: str = "scout",
    ttl: float = 3600.0,
    **kwargs,
) -> AccessToken:
    now = time.time()
    return AccessToken(
        token_id=token_id,
        token_hash=token_hash,
        token_suffix=token_id[-4:],
        agent_name=agent_name,
        grantor_id="admin-1",
        scopes=["chat"],
        expires_at=now + ttl,
        created_at=now,
        **kwargs,
    )


def _make_trigger(source: str = "slack", **kwargs) -> TriggerSpec:
    return TriggerSpec(
        source=source,
        strategy=Strategy.WEBHOOK,
        filter=EventFilter(channels=["#support"]),
        target="support-agent",
        action="chat",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# PostgresTokenStore — save / get / list / revoke / delete / cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_store_save_get(dialect_engine):
    store = PostgresTokenStore(engine=dialect_engine)
    await store.init()

    token = _make_token()
    await store.save(token)

    result = await store.get(token.token_id)
    assert result is not None
    assert result.token_id == token.token_id
    assert result.token_hash == token.token_hash
    assert result.agent_name == "scout"
    assert result.scopes == ["chat"]


@pytest.mark.asyncio
async def test_token_store_get_missing(dialect_engine):
    store = PostgresTokenStore(engine=dialect_engine)
    await store.init()

    result = await store.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_token_store_get_by_hash(dialect_engine):
    store = PostgresTokenStore(engine=dialect_engine)
    await store.init()

    token = _make_token(token_id="tok-002", token_hash="hash-bbb")
    await store.save(token)

    result = await store.get_by_hash("hash-bbb")
    assert result is not None
    assert result.token_id == "tok-002"


@pytest.mark.asyncio
async def test_token_store_get_by_hash_missing(dialect_engine):
    store = PostgresTokenStore(engine=dialect_engine)
    await store.init()

    result = await store.get_by_hash("hash-nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_token_store_save_conflict_update_on_token_id(dialect_engine):
    """Second save on same token_id should update status/used_at, not error."""
    store = PostgresTokenStore(engine=dialect_engine)
    await store.init()

    token = _make_token(token_id="tok-003", token_hash="hash-ccc")
    await store.save(token)

    # Update status + used_at
    token.status = TokenStatus.USED
    token.used_at = time.time()
    await store.save(token)

    result = await store.get("tok-003")
    assert result is not None
    assert result.status == TokenStatus.USED
    assert result.used_at is not None


@pytest.mark.asyncio
async def test_token_store_list_all(dialect_engine):
    store = PostgresTokenStore(engine=dialect_engine)
    await store.init()

    t1 = _make_token(token_id="tok-010", token_hash="hash-010", agent_name="scout")
    t2 = _make_token(token_id="tok-011", token_hash="hash-011", agent_name="scout")
    t3 = _make_token(token_id="tok-012", token_hash="hash-012", agent_name="other")
    await store.save(t1)
    await store.save(t2)
    await store.save(t3)

    all_tokens = await store.list_tokens()
    assert len(all_tokens) >= 3
    ids = {t.token_id for t in all_tokens}
    assert {"tok-010", "tok-011", "tok-012"}.issubset(ids)


@pytest.mark.asyncio
async def test_token_store_list_filtered_by_agent(dialect_engine):
    store = PostgresTokenStore(engine=dialect_engine)
    await store.init()

    t1 = _make_token(token_id="tok-020", token_hash="hash-020", agent_name="agent-a")
    t2 = _make_token(token_id="tok-021", token_hash="hash-021", agent_name="agent-b")
    await store.save(t1)
    await store.save(t2)

    results = await store.list_tokens(agent_name="agent-a")
    ids = {t.token_id for t in results}
    assert "tok-020" in ids
    assert "tok-021" not in ids


@pytest.mark.asyncio
async def test_token_store_revoke(dialect_engine):
    store = PostgresTokenStore(engine=dialect_engine)
    await store.init()

    token = _make_token(token_id="tok-030", token_hash="hash-030")
    await store.save(token)

    await store.revoke("tok-030")

    result = await store.get("tok-030")
    assert result is not None
    assert result.status == TokenStatus.REVOKED


@pytest.mark.asyncio
async def test_token_store_delete(dialect_engine):
    store = PostgresTokenStore(engine=dialect_engine)
    await store.init()

    token = _make_token(token_id="tok-040", token_hash="hash-040")
    await store.save(token)

    await store.delete("tok-040")

    result = await store.get("tok-040")
    assert result is None


@pytest.mark.asyncio
async def test_token_store_cleanup_expired(dialect_engine):
    store = PostgresTokenStore(engine=dialect_engine)
    await store.init()

    # Save a token that is already expired (expires_at in the past)
    now = time.time()
    token = _make_token(token_id="tok-exp-001", token_hash="hash-exp-001")
    token = AccessToken(
        token_id="tok-exp-001",
        token_hash="hash-exp-001",
        token_suffix="0001",
        agent_name="scout",
        grantor_id="admin-1",
        scopes=["chat"],
        expires_at=now - 100,  # already expired
        created_at=now - 200,
    )
    await store.save(token)

    count = await store.cleanup_expired()
    assert count >= 1

    result = await store.get("tok-exp-001")
    assert result is None


@pytest.mark.asyncio
async def test_token_store_timestamp_roundtrip(dialect_engine):
    """expires_at / created_at survive the DB round-trip as floats."""
    store = PostgresTokenStore(engine=dialect_engine)
    await store.init()

    now = time.time()
    expires = now + 7200.0
    token = AccessToken(
        token_id="tok-ts-001",
        token_hash="hash-ts-001",
        token_suffix="0001",
        agent_name="scout",
        grantor_id="admin-1",
        scopes=["chat"],
        expires_at=expires,
        created_at=now,
    )
    await store.save(token)

    result = await store.get("tok-ts-001")
    assert result is not None
    # Allow 1-second tolerance for rounding
    assert abs(result.expires_at - expires) < 1.0
    assert abs(result.created_at - now) < 1.0


# ---------------------------------------------------------------------------
# PostgresTriggerStore — save / get / list / delete / JSON round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_store_save_get(dialect_engine):
    store = PostgresTriggerStore(engine=dialect_engine)
    await store.init()

    trigger = _make_trigger()
    await store.save("t-001", trigger)

    result = await store.get("t-001")
    assert result is not None
    assert result.source == "slack"
    assert result.strategy == Strategy.WEBHOOK
    assert result.target == "support-agent"
    assert result.action == "chat"
    assert result.enabled is True


@pytest.mark.asyncio
async def test_trigger_store_get_missing(dialect_engine):
    store = PostgresTriggerStore(engine=dialect_engine)
    await store.init()

    result = await store.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_trigger_store_save_conflict_update_on_id(dialect_engine):
    """Second save on same id should update, not error."""
    store = PostgresTriggerStore(engine=dialect_engine)
    await store.init()

    trigger = _make_trigger(source="slack")
    await store.save("t-002", trigger)

    trigger2 = _make_trigger(source="email", enabled=False)
    await store.save("t-002", trigger2)

    result = await store.get("t-002")
    assert result is not None
    assert result.source == "email"
    assert result.enabled is False


@pytest.mark.asyncio
async def test_trigger_store_list_all(dialect_engine):
    store = PostgresTriggerStore(engine=dialect_engine)
    await store.init()

    t1 = _make_trigger(source="slack")
    t2 = _make_trigger(source="email")
    await store.save("t-010", t1)
    await store.save("t-011", t2)

    items = await store.list_all()
    assert len(items) >= 2
    ids = {tid for tid, _ in items}
    assert {"t-010", "t-011"}.issubset(ids)


@pytest.mark.asyncio
async def test_trigger_store_delete(dialect_engine):
    store = PostgresTriggerStore(engine=dialect_engine)
    await store.init()

    trigger = _make_trigger()
    await store.save("t-del-001", trigger)

    await store.delete("t-del-001")

    result = await store.get("t-del-001")
    assert result is None


@pytest.mark.asyncio
async def test_trigger_store_json_roundtrip(dialect_engine):
    """filter and context survive the DB round-trip as Python dicts."""
    store = PostgresTriggerStore(engine=dialect_engine)
    await store.init()

    ctx = {"yaml": "steps:\n  - name: greet\n", "extra": 42}
    trigger = TriggerSpec(
        source="shopify",
        strategy=Strategy.POLLER,
        poll_interval=timedelta(seconds=60),
        filter=EventFilter(channels=["#orders"], keywords=["urgent"]),
        target="order-agent",
        action="run_workflow",
        context=ctx,
    )
    await store.save("t-json-001", trigger)

    result = await store.get("t-json-001")
    assert result is not None
    assert result.filter.channels == ["#orders"]
    assert result.filter.keywords == ["urgent"]
    assert result.context == ctx
    assert result.poll_interval == timedelta(seconds=60)


@pytest.mark.asyncio
async def test_trigger_store_poll_interval_none(dialect_engine):
    """poll_interval_seconds=None survives round-trip as None."""
    store = PostgresTriggerStore(engine=dialect_engine)
    await store.init()

    # _make_trigger already defaults to WEBHOOK with no poll_interval
    trigger = _make_trigger()
    # poll_interval is None (no override needed)
    await store.save("t-poll-none", trigger)

    result = await store.get("t-poll-none")
    assert result is not None
    assert result.poll_interval is None


# ---------------------------------------------------------------------------
# Migration-faithful upsert correctness for PostgresTokenStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_upsert_against_migration_schema():
    """Prove ON CONFLICT (token_id) works on a table built exactly as migration 001.

    The parity ``dialect_engine`` fixture uses ``Base.metadata.create_all()``.
    This test bypasses ``create_all`` and reconstructs ``agent_access_tokens``
    with only the constraints that migration 001 creates:

    * ``token_id`` TEXT PRIMARY KEY
    * unique index on ``token_hash``
    * non-unique indexes on project_id / agent_name / status / expires_at

    Saving the same token_id twice must update the row (upsert on PK), not
    raise "no unique or exclusion constraint matching the ON CONFLICT specification".
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
            await conn.execute(text("DROP TABLE IF EXISTS agent_access_tokens CASCADE"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE agent_access_tokens (
                        token_id     TEXT PRIMARY KEY,
                        project_id   TEXT NOT NULL DEFAULT 'default',
                        token_hash   TEXT NOT NULL UNIQUE,
                        token_suffix VARCHAR(4) NOT NULL DEFAULT '',
                        agent_name   TEXT NOT NULL,
                        grantor_id   TEXT NOT NULL,
                        scopes       TEXT[] DEFAULT '{}',
                        status       TEXT DEFAULT 'active',
                        single_use   BOOLEAN DEFAULT false,
                        expires_at   TIMESTAMPTZ NOT NULL,
                        used_at      TIMESTAMPTZ,
                        created_at   TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX idx_access_tokens_hash"
                    " ON agent_access_tokens (token_hash)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX idx_access_tokens_project_id"
                    " ON agent_access_tokens (project_id)"
                )
            )

        store = PostgresTokenStore(engine=engine)
        now = time.time()
        token = AccessToken(
            token_id="mig-faithful-token",
            token_hash="mig-hash-aaa",
            token_suffix="aaaa",
            agent_name="scout",
            grantor_id="admin-1",
            scopes=["chat"],
            expires_at=now + 3600,
            created_at=now,
        )
        await store.save(token)

        # Second save — update status to USED
        token.status = TokenStatus.USED
        token.used_at = now + 10
        # Must NOT raise — ON CONFLICT (token_id) targets the PK.
        await store.save(token)

        result = await store.get("mig-faithful-token")
        assert result is not None
        assert result.status == TokenStatus.USED, (
            "upsert should have updated status to 'used'"
        )

        # Exactly one row
        from sagewai.db.models import AgentAccessTokenModel

        tbl = AgentAccessTokenModel.__table__
        async with engine.connect() as conn:
            count = (
                await conn.execute(select(func.count()).select_from(tbl))
            ).scalar_one()
        assert count == 1, f"expected 1 row after upsert, got {count}"
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS agent_access_tokens CASCADE"))
        await engine.dispose()


# ---------------------------------------------------------------------------
# Migration-faithful upsert correctness for PostgresTriggerStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_upsert_against_migration_schema():
    """Prove ON CONFLICT (id) works on a table built exactly as migration 001.

    Reconstructs ``connector_triggers`` with only the constraints from migration 001:

    * ``id`` VARCHAR(36) PRIMARY KEY
    * non-unique index on ``project_id``
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
            await conn.execute(text("DROP TABLE IF EXISTS connector_triggers CASCADE"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE connector_triggers (
                        id                    VARCHAR(36) PRIMARY KEY,
                        project_id            TEXT NOT NULL DEFAULT 'default',
                        source                VARCHAR(100) NOT NULL,
                        strategy              VARCHAR(20) NOT NULL,
                        poll_interval_seconds INTEGER,
                        filter_json           JSON NOT NULL DEFAULT '{}',
                        target                VARCHAR(200) NOT NULL,
                        action                VARCHAR(50) NOT NULL,
                        context_json          JSON NOT NULL DEFAULT '{}',
                        enabled               BOOLEAN NOT NULL DEFAULT true,
                        created_at            TIMESTAMPTZ DEFAULT now(),
                        updated_at            TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX ix_connector_triggers_project_id"
                    " ON connector_triggers (project_id)"
                )
            )

        store = PostgresTriggerStore(engine=engine)
        trigger = _make_trigger(source="slack")
        await store.save("mig-trig-001", trigger)

        # Second save — update source; must NOT raise.
        trigger2 = _make_trigger(source="email", enabled=False)
        await store.save("mig-trig-001", trigger2)

        result = await store.get("mig-trig-001")
        assert result is not None
        assert result.source == "email", "upsert should have updated source to 'email'"
        assert result.enabled is False

        # Exactly one row
        from sagewai.db.models import ConnectorTriggerModel

        tbl = ConnectorTriggerModel.__table__
        async with engine.connect() as conn:
            count = (
                await conn.execute(select(func.count()).select_from(tbl))
            ).scalar_one()
        assert count == 1, f"expected 1 row after upsert, got {count}"
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS connector_triggers CASCADE"))
        await engine.dispose()
