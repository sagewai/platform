# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for the 4 connector stores — runs against both SQLite and Postgres.

Covers: PostgresCredentialStore, PostgresOAuthTokenStore,
        PostgresCustomConnectorStore, PostgresCursorStore.
Uses the dialect_engine fixture from tests/db/conftest.py (SQLite always;
Postgres when SAGEWAI_TEST_DATABASE_URL is set).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.connectors.base import TokenSet
from sagewai.connectors.pg_stores import (
    PostgresCredentialStore,
    PostgresCursorStore,
    PostgresCustomConnectorStore,
    PostgresOAuthTokenStore,
)


# ---------------------------------------------------------------------------
# PostgresCredentialStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_credential_store_put_get(dialect_engine):
    store = PostgresCredentialStore(engine=dialect_engine)
    await store.init()

    await store.put("slack", {"bot_token": "xoxb-123"}, project_id="proj1")
    result = await store.get("slack", project_id="proj1")
    assert result == {"bot_token": "xoxb-123"}


@pytest.mark.asyncio
async def test_credential_store_get_missing(dialect_engine):
    store = PostgresCredentialStore(engine=dialect_engine)
    await store.init()

    result = await store.get("nonexistent", project_id="proj1")
    assert result is None


@pytest.mark.asyncio
async def test_credential_store_put_conflict_update(dialect_engine):
    """Second put on same (project_id, connector_name) should update, not error."""
    store = PostgresCredentialStore(engine=dialect_engine)
    await store.init()

    await store.put("slack", {"bot_token": "xoxb-old"}, project_id="proj1")
    await store.put("slack", {"bot_token": "xoxb-new"}, project_id="proj1")
    result = await store.get("slack", project_id="proj1")
    assert result == {"bot_token": "xoxb-new"}


@pytest.mark.asyncio
async def test_credential_store_json_roundtrip(dialect_engine):
    """JSON values survive the round-trip as Python objects."""
    store = PostgresCredentialStore(engine=dialect_engine)
    await store.init()

    creds = {"api_key": "sk-abc", "region": "us-east-1", "quota": "100"}
    await store.put("aws", creds, project_id="proj1")
    result = await store.get("aws", project_id="proj1")
    assert result == creds


@pytest.mark.asyncio
async def test_credential_store_delete(dialect_engine):
    store = PostgresCredentialStore(engine=dialect_engine)
    await store.init()

    await store.put("slack", {"bot_token": "xoxb-123"}, project_id="proj1")
    await store.delete("slack", project_id="proj1")
    result = await store.get("slack", project_id="proj1")
    assert result is None


@pytest.mark.asyncio
async def test_credential_store_list_all(dialect_engine):
    store = PostgresCredentialStore(engine=dialect_engine)
    await store.init()

    await store.put("slack", {"bot_token": "xoxb-1"}, project_id="proj1")
    await store.put("email", {"api_key": "SG.abc"}, project_id="proj1")
    # Different project — should not appear
    await store.put("slack", {"bot_token": "other"}, project_id="proj2")

    items = await store.list_all(project_id="proj1")
    assert len(items) == 2
    names = {s.connector_name for s in items}
    assert names == {"slack", "email"}
    # All entries have has_credentials=True
    assert all(s.has_credentials for s in items)


@pytest.mark.asyncio
async def test_credential_store_project_isolation(dialect_engine):
    """Credentials in different projects don't cross-contaminate."""
    store = PostgresCredentialStore(engine=dialect_engine)
    await store.init()

    await store.put("slack", {"token": "p1"}, project_id="proj1")
    await store.put("slack", {"token": "p2"}, project_id="proj2")

    r1 = await store.get("slack", project_id="proj1")
    r2 = await store.get("slack", project_id="proj2")
    assert r1 == {"token": "p1"}
    assert r2 == {"token": "p2"}


# ---------------------------------------------------------------------------
# PostgresOAuthTokenStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_store_save_get(dialect_engine):
    # Need a credential row first (oauth store UPDATE path requires the row to exist)
    cred_store = PostgresCredentialStore(engine=dialect_engine)
    await cred_store.init()
    await cred_store.put("github", {}, project_id="proj1")

    store = PostgresOAuthTokenStore(engine=dialect_engine)
    ts = TokenSet(
        access_token="ghu_abc123",
        refresh_token="ghr_xyz",
        token_type="Bearer",
        scope="repo,user",
    )
    await store.save_token("github", ts, project_id="proj1")
    result = await store.get_token("github", project_id="proj1")
    assert result is not None
    assert result.access_token == "ghu_abc123"
    assert result.refresh_token == "ghr_xyz"
    assert result.scope == "repo,user"
    assert result.token_type == "Bearer"


@pytest.mark.asyncio
async def test_oauth_store_get_missing(dialect_engine):
    cred_store = PostgresCredentialStore(engine=dialect_engine)
    await cred_store.init()

    store = PostgresOAuthTokenStore(engine=dialect_engine)
    result = await store.get_token("nonexistent", project_id="proj1")
    assert result is None


@pytest.mark.asyncio
async def test_oauth_store_needs_refresh_no_token(dialect_engine):
    cred_store = PostgresCredentialStore(engine=dialect_engine)
    await cred_store.init()

    store = PostgresOAuthTokenStore(engine=dialect_engine)
    assert await store.needs_refresh("nonexistent", project_id="proj1") is True


@pytest.mark.asyncio
async def test_oauth_store_needs_refresh_no_expiry(dialect_engine):
    cred_store = PostgresCredentialStore(engine=dialect_engine)
    await cred_store.init()
    await cred_store.put("github", {}, project_id="proj1")

    store = PostgresOAuthTokenStore(engine=dialect_engine)
    ts = TokenSet(access_token="abc")  # expires_at=None
    await store.save_token("github", ts, project_id="proj1")
    assert await store.needs_refresh("github", project_id="proj1") is False


@pytest.mark.asyncio
async def test_oauth_store_needs_refresh_expired(dialect_engine):
    cred_store = PostgresCredentialStore(engine=dialect_engine)
    await cred_store.init()
    await cred_store.put("github", {}, project_id="proj1")

    store = PostgresOAuthTokenStore(engine=dialect_engine)
    # expires_at in the past
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    ts = TokenSet(access_token="abc", expires_at=past)
    await store.save_token("github", ts, project_id="proj1")
    assert await store.needs_refresh("github", project_id="proj1") is True


@pytest.mark.asyncio
async def test_oauth_store_save_updates_existing(dialect_engine):
    cred_store = PostgresCredentialStore(engine=dialect_engine)
    await cred_store.init()
    await cred_store.put("github", {}, project_id="proj1")

    store = PostgresOAuthTokenStore(engine=dialect_engine)
    ts1 = TokenSet(access_token="token-v1")
    ts2 = TokenSet(access_token="token-v2", refresh_token="refresh-v2")
    await store.save_token("github", ts1, project_id="proj1")
    await store.save_token("github", ts2, project_id="proj1")
    result = await store.get_token("github", project_id="proj1")
    assert result.access_token == "token-v2"
    assert result.refresh_token == "refresh-v2"


# ---------------------------------------------------------------------------
# PostgresCustomConnectorStore
# ---------------------------------------------------------------------------


def _make_spec(name: str = "my-connector", **overrides) -> dict:
    spec = {
        "name": name,
        "display_name": "My Connector",
        "category": "custom",
        "description": "A test connector",
        "auth_type": "api_key",
        "auth_fields": [{"key": "api_key", "label": "API Key", "env_var": "MY_KEY"}],
        "mcp_command": ["npx", "-y", "my-connector"],
        "docs_url": "https://example.com/docs",
        "agent_description": "Useful for testing",
        "example_prompt": "Do something with my connector",
        "oauth_authorize_url": None,
        "oauth_token_url": None,
        "oauth_scopes": [],
        "supports_webhook": False,
        "supports_listener": True,
        "supports_poller": False,
    }
    spec.update(overrides)
    return spec


@pytest.mark.asyncio
async def test_custom_connector_save_get(dialect_engine):
    store = PostgresCustomConnectorStore(engine=dialect_engine)
    await store.init()

    spec = _make_spec()
    await store.save(spec, project_id="proj1")
    result = await store.get("my-connector", project_id="proj1")
    assert result is not None
    assert result["name"] == "my-connector"
    assert result["display_name"] == "My Connector"
    assert result["supports_listener"] is True


@pytest.mark.asyncio
async def test_custom_connector_json_roundtrip(dialect_engine):
    """auth_fields and mcp_command round-trip as Python lists."""
    store = PostgresCustomConnectorStore(engine=dialect_engine)
    await store.init()

    spec = _make_spec()
    await store.save(spec, project_id="proj1")
    result = await store.get("my-connector", project_id="proj1")
    assert result["auth_fields"] == spec["auth_fields"]
    assert result["mcp_command"] == spec["mcp_command"]
    assert result["oauth_scopes"] == []


@pytest.mark.asyncio
async def test_custom_connector_upsert_updates(dialect_engine):
    """Second save on same (project_id, name) should update existing row."""
    store = PostgresCustomConnectorStore(engine=dialect_engine)
    await store.init()

    spec = _make_spec(display_name="Old Name")
    await store.save(spec, project_id="proj1")
    spec2 = _make_spec(display_name="New Name")
    await store.save(spec2, project_id="proj1")
    result = await store.get("my-connector", project_id="proj1")
    assert result["display_name"] == "New Name"


@pytest.mark.asyncio
async def test_custom_connector_get_missing(dialect_engine):
    store = PostgresCustomConnectorStore(engine=dialect_engine)
    await store.init()

    result = await store.get("nonexistent", project_id="proj1")
    assert result is None


@pytest.mark.asyncio
async def test_custom_connector_list_all(dialect_engine):
    store = PostgresCustomConnectorStore(engine=dialect_engine)
    await store.init()

    await store.save(_make_spec("connector-a"), project_id="proj1")
    await store.save(_make_spec("connector-b"), project_id="proj1")
    await store.save(_make_spec("connector-c"), project_id="proj2")

    items = await store.list_all(project_id="proj1")
    assert len(items) == 2
    names = {i["name"] for i in items}
    assert names == {"connector-a", "connector-b"}


@pytest.mark.asyncio
async def test_custom_connector_delete(dialect_engine):
    store = PostgresCustomConnectorStore(engine=dialect_engine)
    await store.init()

    await store.save(_make_spec(), project_id="proj1")
    await store.delete("my-connector", project_id="proj1")
    result = await store.get("my-connector", project_id="proj1")
    assert result is None


@pytest.mark.asyncio
async def test_custom_connector_list_ordered_by_name(dialect_engine):
    store = PostgresCustomConnectorStore(engine=dialect_engine)
    await store.init()

    await store.save(_make_spec("zzz-last"), project_id="proj1")
    await store.save(_make_spec("aaa-first"), project_id="proj1")
    items = await store.list_all(project_id="proj1")
    assert [i["name"] for i in items] == ["aaa-first", "zzz-last"]


# ---------------------------------------------------------------------------
# PostgresCursorStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cursor_store_set_get(dialect_engine):
    store = PostgresCursorStore(engine=dialect_engine)
    await store.init()

    await store.set("slack", "#general", "ts-12345", project_id="proj1")
    result = await store.get("slack", "#general", project_id="proj1")
    assert result == "ts-12345"


@pytest.mark.asyncio
async def test_cursor_store_get_missing(dialect_engine):
    store = PostgresCursorStore(engine=dialect_engine)
    await store.init()

    result = await store.get("slack", "#unknown", project_id="proj1")
    assert result is None


@pytest.mark.asyncio
async def test_cursor_store_upsert_conflict_update(dialect_engine):
    """Set on same 3-col key updates the cursor_value."""
    store = PostgresCursorStore(engine=dialect_engine)
    await store.init()

    await store.set("slack", "#general", "ts-old", project_id="proj1")
    await store.set("slack", "#general", "ts-new", project_id="proj1")
    result = await store.get("slack", "#general", project_id="proj1")
    assert result == "ts-new"


@pytest.mark.asyncio
async def test_cursor_store_channel_isolation(dialect_engine):
    """Different channels on same connector are independent."""
    store = PostgresCursorStore(engine=dialect_engine)
    await store.init()

    await store.set("slack", "#general", "ts-gen", project_id="proj1")
    await store.set("slack", "#random", "ts-rand", project_id="proj1")

    assert await store.get("slack", "#general", project_id="proj1") == "ts-gen"
    assert await store.get("slack", "#random", project_id="proj1") == "ts-rand"


@pytest.mark.asyncio
async def test_cursor_store_project_isolation(dialect_engine):
    store = PostgresCursorStore(engine=dialect_engine)
    await store.init()

    await store.set("slack", "#general", "ts-p1", project_id="proj1")
    await store.set("slack", "#general", "ts-p2", project_id="proj2")

    assert await store.get("slack", "#general", project_id="proj1") == "ts-p1"
    assert await store.get("slack", "#general", project_id="proj2") == "ts-p2"


# ---------------------------------------------------------------------------
# Migration-faithful upsert correctness for PostgresCustomConnectorStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_connector_upsert_against_migration_schema():
    """Prove ON CONFLICT (name) works on a table built exactly as migration 001.

    The parity ``dialect_engine`` fixture calls ``Base.metadata.create_all()``,
    which on PostgreSQL would create the model-defined schema (potentially with
    indices added by the model).  This test bypasses ``create_all`` entirely and
    reconstructs ``custom_connectors`` with only the constraints that migration
    001 actually creates on a production database:

    * ``name`` PRIMARY KEY
    * non-unique index on ``project_id``
    * NO unique constraint or index on ``(project_id, name)``

    Saving the same connector name twice must update the row (upsert), not raise
    "no unique or exclusion constraint matching the ON CONFLICT specification".
    This is the exact failure mode the bug introduced.
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
        # Build the table exactly as migration 001 does — no extra unique index.
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS custom_connectors CASCADE"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE custom_connectors (
                        name            VARCHAR(100) PRIMARY KEY,
                        project_id      TEXT NOT NULL DEFAULT 'default',
                        display_name    TEXT NOT NULL DEFAULT '',
                        category        VARCHAR(100) NOT NULL DEFAULT 'custom',
                        description     TEXT NOT NULL DEFAULT '',
                        auth_type       VARCHAR(20) NOT NULL DEFAULT 'api_key',
                        auth_fields_json  JSONB NOT NULL DEFAULT '[]',
                        mcp_command_json  JSONB NOT NULL DEFAULT '[]',
                        docs_url          TEXT,
                        agent_description TEXT NOT NULL DEFAULT '',
                        example_prompt    TEXT NOT NULL DEFAULT '',
                        oauth_authorize_url TEXT,
                        oauth_token_url     TEXT,
                        oauth_scopes_json   JSONB DEFAULT '[]',
                        supports_webhook  BOOLEAN NOT NULL DEFAULT false,
                        supports_listener BOOLEAN NOT NULL DEFAULT false,
                        supports_poller   BOOLEAN NOT NULL DEFAULT false,
                        created_at TIMESTAMPTZ DEFAULT now(),
                        updated_at TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX ix_custom_connectors_project_id"
                    " ON custom_connectors (project_id)"
                )
            )

        store = PostgresCustomConnectorStore(engine=engine)
        spec = {
            "name": "migration-faithful-connector",
            "display_name": "First",
            "category": "custom",
            "description": "migration-faithful test",
            "auth_type": "api_key",
            "auth_fields": [],
            "mcp_command": [],
            "agent_description": "",
            "example_prompt": "",
        }
        await store.save(spec, project_id="proj1")
        spec["display_name"] = "Second"
        # This second save must NOT raise — ON CONFLICT (name) targets the PK.
        await store.save(spec, project_id="proj1")

        result = await store.get("migration-faithful-connector", project_id="proj1")
        assert result is not None
        assert result["display_name"] == "Second", (
            "upsert should have updated display_name to 'Second'"
        )

        # Confirm exactly one row — upsert, not duplicate insert.
        from sagewai.db.models import CustomConnectorModel

        tbl = CustomConnectorModel.__table__
        async with engine.connect() as conn:
            count = (
                await conn.execute(select(func.count()).select_from(tbl))
            ).scalar_one()
        assert count == 1, f"expected 1 row after upsert, got {count}"
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS custom_connectors CASCADE"))
        await engine.dispose()
