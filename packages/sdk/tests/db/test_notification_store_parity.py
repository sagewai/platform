# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for PostgresNotificationStore — runs against both SQLite and Postgres.

Covers: history append/list (with filtering), channel config upsert
(conflict on the real unique constraint) + get + list + delete, trigger
routing save + list + delete, JSON round-trip, and migration-faithful
upsert tests that prove the unique constraints accepted by the migration
are the same ones used by the store.

Uses the ``dialect_engine`` fixture from tests/db/conftest.py
(SQLite always; Postgres when SAGEWAI_TEST_DATABASE_URL is set).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.notifications.models import NotificationRecord
from sagewai.notifications.postgres_store import PostgresNotificationStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**kwargs) -> NotificationRecord:
    defaults = {
        "id": "n1",
        "trigger": "budget_warning",
        "title": "Alert",
        "body": "Limit approaching",
        "severity": "warning",
        "channel_type": "email",
        "project_id": "proj1",
    }
    defaults.update(kwargs)
    return NotificationRecord(**defaults)


# ---------------------------------------------------------------------------
# History — append / list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_record_and_list(dialect_engine):
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    rec = _make_record(id="h1", trigger="budget_warning", title="Budget alert")
    await store.record(rec)

    history = await store.list_history()
    assert len(history) == 1
    assert history[0]["trigger"] == "budget_warning"
    assert history[0]["title"] == "Budget alert"
    assert history[0]["project_id"] == "proj1"
    assert history[0]["delivered"] is False


@pytest.mark.asyncio
async def test_history_newest_first(dialect_engine):
    """list_history returns rows ordered by created_at DESC (newest first)."""
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    t1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    await store.record(_make_record(id="old", title="Old", created_at=t1))
    await store.record(_make_record(id="new", title="New", created_at=t2))

    history = await store.list_history()
    assert history[0]["title"] == "New"
    assert history[1]["title"] == "Old"


@pytest.mark.asyncio
async def test_history_filter_by_trigger(dialect_engine):
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    await store.record(_make_record(id="a", trigger="budget_warning", title="A"))
    await store.record(_make_record(id="b", trigger="workflow_failed", title="B"))

    result = await store.list_history(trigger="budget_warning")
    assert len(result) == 1
    assert result[0]["trigger"] == "budget_warning"


@pytest.mark.asyncio
async def test_history_filter_by_project_id(dialect_engine):
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    await store.record(_make_record(id="p1", project_id="proj1", title="P1"))
    await store.record(_make_record(id="p2", project_id="proj2", title="P2"))

    result = await store.list_history(project_id="proj1")
    assert len(result) == 1
    assert result[0]["project_id"] == "proj1"


@pytest.mark.asyncio
async def test_history_limit_and_offset(dialect_engine):
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    for i in range(5):
        await store.record(_make_record(id=f"r{i}", title=f"T{i}"))

    page1 = await store.list_history(limit=3, offset=0)
    page2 = await store.list_history(limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 2


@pytest.mark.asyncio
async def test_history_created_at_iso_string(dialect_engine):
    """created_at is returned as an ISO 8601 string."""
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    await store.record(_make_record(id="ts1"))
    history = await store.list_history()
    ts = history[0]["created_at"]
    assert isinstance(ts, str)
    # Must be parseable
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# Channel config — upsert / get / list / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_config_save_and_list(dialect_engine):
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    cfg = {
        "project_id": "proj1",
        "channel_type": "email",
        "enabled": True,
        "smtp_host": "smtp.example.com",
    }
    row_id = await store.save_channel_config(cfg)
    assert isinstance(row_id, int)
    assert row_id > 0

    configs = await store.list_channel_configs(project_id="proj1")
    assert len(configs) == 1
    assert configs[0]["channel_type"] == "email"
    assert configs[0]["smtp_host"] == "smtp.example.com"
    assert configs[0]["enabled"] is True


@pytest.mark.asyncio
async def test_channel_config_upsert_on_real_unique_constraint(dialect_engine):
    """Upsert conflicts on (project_id, channel_type) — the real migration constraint.

    This is a migration-faithful upsert test: we call save_channel_config twice
    with the same (project_id, channel_type) and verify the second call updates
    the existing row rather than inserting a duplicate (no IntegrityError).
    """
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    cfg1 = {"project_id": "proj1", "channel_type": "slack", "webhook_url": "https://old"}
    cfg2 = {"project_id": "proj1", "channel_type": "slack", "webhook_url": "https://new"}

    id1 = await store.save_channel_config(cfg1)
    id2 = await store.save_channel_config(cfg2)

    # Same row must have been updated — same id, updated value
    assert id1 == id2

    configs = await store.list_channel_configs(project_id="proj1")
    assert len(configs) == 1
    assert configs[0]["webhook_url"] == "https://new"


@pytest.mark.asyncio
async def test_channel_config_different_project_ids_are_separate(dialect_engine):
    """(proj1, email) and (proj2, email) are two distinct rows, not an upsert conflict."""
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    await store.save_channel_config({"project_id": "proj1", "channel_type": "email"})
    await store.save_channel_config({"project_id": "proj2", "channel_type": "email"})

    all_configs = await store.list_channel_configs()
    assert len(all_configs) == 2


@pytest.mark.asyncio
async def test_channel_config_list_filters_by_project(dialect_engine):
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    await store.save_channel_config({"project_id": "proj1", "channel_type": "email"})
    await store.save_channel_config({"project_id": "proj2", "channel_type": "slack"})

    p1 = await store.list_channel_configs(project_id="proj1")
    assert len(p1) == 1
    assert p1[0]["project_id"] == "proj1"


@pytest.mark.asyncio
async def test_channel_config_delete(dialect_engine):
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    row_id = await store.save_channel_config(
        {"project_id": "proj1", "channel_type": "email"}
    )
    assert await store.delete_channel_config(row_id) is True
    assert await store.delete_channel_config(row_id) is False  # already gone
    assert await store.list_channel_configs(project_id="proj1") == []


@pytest.mark.asyncio
async def test_channel_config_json_roundtrip(dialect_engine):
    """Non-core fields survive as a Python dict through the JSON column."""
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    cfg = {
        "project_id": "proj1",
        "channel_type": "email",
        "to_addresses": ["a@b.com", "c@d.com"],
        "smtp_port": 587,
    }
    await store.save_channel_config(cfg)
    configs = await store.list_channel_configs(project_id="proj1")
    assert configs[0]["to_addresses"] == ["a@b.com", "c@d.com"]
    assert configs[0]["smtp_port"] == 587


# ---------------------------------------------------------------------------
# Trigger routing — save / list / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_routing_save_and_list(dialect_engine):
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    cfg = {
        "project_id": "proj1",
        "trigger": "budget_warning",
        "channel_type": "email",
        "enabled": True,
    }
    row_id = await store.save_trigger_routing(cfg)
    assert isinstance(row_id, int)
    assert row_id > 0

    routes = await store.list_trigger_routing(project_id="proj1")
    assert len(routes) == 1
    assert routes[0]["trigger"] == "budget_warning"
    assert routes[0]["channel_type"] == "email"
    assert routes[0]["enabled"] is True


@pytest.mark.asyncio
async def test_trigger_routing_upsert_on_real_unique_constraint(dialect_engine):
    """Upsert conflicts on (project_id, trigger, channel_type) — the real migration constraint.

    Migration-faithful test: two saves with the same composite key must update,
    not insert, and list must still show exactly one row.
    """
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    cfg1 = {
        "project_id": "proj1",
        "trigger": "budget_warning",
        "channel_type": "slack",
        "enabled": True,
    }
    cfg2 = {**cfg1, "enabled": False}

    id1 = await store.save_trigger_routing(cfg1)
    id2 = await store.save_trigger_routing(cfg2)

    assert id1 == id2

    routes = await store.list_trigger_routing(project_id="proj1")
    assert len(routes) == 1
    assert routes[0]["enabled"] is False


@pytest.mark.asyncio
async def test_trigger_routing_filter_by_project(dialect_engine):
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    await store.save_trigger_routing(
        {"project_id": "proj1", "trigger": "budget_warning", "channel_type": "email"}
    )
    await store.save_trigger_routing(
        {"project_id": "proj2", "trigger": "budget_warning", "channel_type": "slack"}
    )

    p1 = await store.list_trigger_routing(project_id="proj1")
    assert len(p1) == 1
    assert p1[0]["project_id"] == "proj1"


@pytest.mark.asyncio
async def test_trigger_routing_delete(dialect_engine):
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    row_id = await store.save_trigger_routing(
        {"project_id": "proj1", "trigger": "budget_warning", "channel_type": "email"}
    )
    assert await store.delete_trigger_routing(row_id) is True
    assert await store.delete_trigger_routing(row_id) is False  # already gone
    assert await store.list_trigger_routing(project_id="proj1") == []


@pytest.mark.asyncio
async def test_trigger_routing_created_at_iso_string(dialect_engine):
    """created_at in trigger routing rows is returned as ISO 8601 string."""
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    await store.save_trigger_routing(
        {"project_id": "proj1", "trigger": "budget_warning", "channel_type": "email"}
    )
    routes = await store.list_trigger_routing(project_id="proj1")
    ts = routes[0]["created_at"]
    assert isinstance(ts, str)
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# Migration-faithful upsert — raw DDL + store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_upsert_conflict_target_matches_migration_constraint(dialect_engine):
    """Verify that the upsert conflict target (project_id, channel_type) works at
    the DDL level — insert two rows with the same composite key, confirm only one
    row is persisted.  This exercises the unique constraint that migration 001
    defines on notification_channels.
    """
    from sagewai.db.models import NotificationChannelModel
    from sagewai.db.dialect import upsert

    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    tbl = NotificationChannelModel.__table__
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    values = {
        "project_id": "proj_raw",
        "channel_type": "in_app",
        "enabled": True,
        "config": {},
        "created_at": now,
        "updated_at": now,
    }
    stmt1 = upsert(
        tbl,
        values,
        index_elements=["project_id", "channel_type"],
        set_={"enabled": True, "config": {}, "updated_at": now},
        dialect=dialect_engine.dialect.name,
    )
    values2 = {**values, "enabled": False}
    stmt2 = upsert(
        tbl,
        values2,
        index_elements=["project_id", "channel_type"],
        set_={"enabled": False, "config": {}, "updated_at": now},
        dialect=dialect_engine.dialect.name,
    )

    async with dialect_engine.begin() as conn:
        await conn.execute(stmt1)
        await conn.execute(stmt2)

    from sqlalchemy import select, func as sa_func
    async with dialect_engine.connect() as conn:
        count = (
            await conn.execute(
                select(sa_func.count()).select_from(tbl).where(
                    tbl.c.project_id == "proj_raw",
                    tbl.c.channel_type == "in_app",
                )
            )
        ).scalar_one()
        row = (
            await conn.execute(
                select(tbl.c.enabled).where(
                    tbl.c.project_id == "proj_raw",
                    tbl.c.channel_type == "in_app",
                )
            )
        ).scalar_one()
    assert count == 1, "upsert should not insert a duplicate row"
    assert row is False, "second upsert should have updated enabled to False"


@pytest.mark.asyncio
async def test_trigger_upsert_conflict_target_matches_migration_constraint(dialect_engine):
    """Verify the trigger upsert conflict target (project_id, trigger, channel_type)
    works at DDL level — confirms migration 001's unique constraint is honoured.
    """
    from sagewai.db.models import NotificationTriggerModel
    from sagewai.db.dialect import upsert

    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    tbl = NotificationTriggerModel.__table__
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    base = {
        "project_id": "proj_raw",
        "trigger": "my_trigger",
        "channel_type": "in_app",
        "enabled": True,
        "created_at": now,
    }
    stmt1 = upsert(
        tbl, base,
        index_elements=["project_id", "trigger", "channel_type"],
        set_={"enabled": True, "created_at": now},
        dialect=dialect_engine.dialect.name,
    )
    values2 = {**base, "enabled": False}
    stmt2 = upsert(
        tbl, values2,
        index_elements=["project_id", "trigger", "channel_type"],
        set_={"enabled": False, "created_at": now},
        dialect=dialect_engine.dialect.name,
    )

    async with dialect_engine.begin() as conn:
        await conn.execute(stmt1)
        await conn.execute(stmt2)

    from sqlalchemy import select, func as sa_func
    async with dialect_engine.connect() as conn:
        count = (
            await conn.execute(
                select(sa_func.count()).select_from(tbl).where(
                    tbl.c.project_id == "proj_raw",
                    tbl.c.trigger == "my_trigger",
                    tbl.c.channel_type == "in_app",
                )
            )
        ).scalar_one()
        row = (
            await conn.execute(
                select(tbl.c.enabled).where(
                    tbl.c.project_id == "proj_raw",
                    tbl.c.trigger == "my_trigger",
                    tbl.c.channel_type == "in_app",
                )
            )
        ).scalar_one()
    assert count == 1, "upsert should not insert a duplicate row"
    assert row is False, "second upsert should have updated enabled to False"


# ---------------------------------------------------------------------------
# Security — encryption round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_config_encryption_roundtrip(dialect_engine):
    """Secret fields are stored as ciphertext and returned as plaintext.

    Proves encryption-at-rest: the raw DB column must not contain the
    secret string, while list_channel_configs must return the plaintext.
    """
    from cryptography.fernet import Fernet
    from sqlalchemy import select as sa_select, text as sa_text

    from sagewai.db.models import NotificationChannelModel

    key = Fernet.generate_key().decode()
    store = PostgresNotificationStore(engine=dialect_engine, encryption_key=key)
    await store.init()

    secret = "s3cr3t-p@ssword"
    cfg = {
        "project_id": "enc_proj",
        "channel_type": "email",
        "smtp_password": secret,
    }
    await store.save_channel_config(cfg)

    # (a) list_channel_configs must return the original plaintext
    configs = await store.list_channel_configs(project_id="enc_proj")
    assert len(configs) == 1
    assert configs[0]["smtp_password"] == secret, (
        "list_channel_configs must decrypt smtp_password back to plaintext"
    )

    # (b) raw value in the DB column must NOT be the plaintext secret
    tbl = NotificationChannelModel.__table__
    async with dialect_engine.connect() as conn:
        raw_config = (
            await conn.execute(
                sa_select(tbl.c.config).where(
                    tbl.c.project_id == "enc_proj",
                    tbl.c.channel_type == "email",
                )
            )
        ).scalar_one()

    import json as _json

    if isinstance(raw_config, str):
        raw_config = _json.loads(raw_config)

    raw_password = raw_config.get("smtp_password", "")
    assert raw_password != secret, (
        "smtp_password must be stored as ciphertext, not plaintext"
    )
    assert raw_password, "smtp_password ciphertext must not be empty"


@pytest.mark.asyncio
async def test_channel_config_webhook_url_encryption_roundtrip(dialect_engine):
    """webhook_url is also encrypted at rest, decrypted on read."""
    from cryptography.fernet import Fernet

    from sagewai.db.models import NotificationChannelModel

    key = Fernet.generate_key().decode()
    store = PostgresNotificationStore(engine=dialect_engine, encryption_key=key)
    await store.init()

    secret_url = "https://hooks.example.com/x?token=abc123"
    cfg = {
        "project_id": "enc_proj2",
        "channel_type": "slack",
        "webhook_url": secret_url,
    }
    await store.save_channel_config(cfg)

    # Plaintext returned on read
    configs = await store.list_channel_configs(project_id="enc_proj2")
    assert configs[0]["webhook_url"] == secret_url

    # Ciphertext in DB
    from sqlalchemy import select as sa_select

    tbl = NotificationChannelModel.__table__
    async with dialect_engine.connect() as conn:
        raw_config = (
            await conn.execute(
                sa_select(tbl.c.config).where(
                    tbl.c.project_id == "enc_proj2",
                    tbl.c.channel_type == "slack",
                )
            )
        ).scalar_one()

    import json as _json

    if isinstance(raw_config, str):
        raw_config = _json.loads(raw_config)

    assert raw_config.get("webhook_url") != secret_url, (
        "webhook_url must be stored as ciphertext, not plaintext"
    )


# ---------------------------------------------------------------------------
# Data integrity — created_at preserved on upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_routing_created_at_preserved_on_upsert(dialect_engine):
    """created_at must not be overwritten when a trigger routing row is updated.

    Save → capture created_at → save again (toggling enabled) → assert
    created_at is unchanged.
    """
    store = PostgresNotificationStore(engine=dialect_engine)
    await store.init()

    cfg = {
        "project_id": "ts_proj",
        "trigger": "budget_warning",
        "channel_type": "email",
        "enabled": True,
    }
    await store.save_trigger_routing(cfg)

    routes_before = await store.list_trigger_routing(project_id="ts_proj")
    created_at_before = routes_before[0]["created_at"]

    # Update (toggle enabled)
    await store.save_trigger_routing({**cfg, "enabled": False})

    routes_after = await store.list_trigger_routing(project_id="ts_proj")
    assert len(routes_after) == 1
    assert routes_after[0]["enabled"] is False
    assert routes_after[0]["created_at"] == created_at_before, (
        "created_at must not be overwritten on upsert update"
    )
