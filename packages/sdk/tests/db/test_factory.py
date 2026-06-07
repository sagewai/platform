# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
import os

import pytest

from sagewai.db import factory

_PG_URL = os.environ.get("SAGEWAI_TEST_DATABASE_URL")


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SAGEWAI_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    factory.reset_engine()
    yield
    factory.reset_engine()


def test_default_url_is_sqlite_in_db_dir():
    from sagewai import home
    url = factory.resolve_database_url()
    assert url.startswith("sqlite+aiosqlite:///")
    assert str(home.db_dir() / "sagewai.db") in url


def test_env_url_takes_over(monkeypatch):
    monkeypatch.setenv("SAGEWAI_DATABASE_URL", "postgresql://u:p@h/db")
    assert factory.resolve_database_url() == "postgresql://u:p@h/db"


# ---------------------------------------------------------------------------
# FIX 1 — DATABASE_URL compatibility alias
# ---------------------------------------------------------------------------

def test_database_url_alias_used_when_sagewai_unset(monkeypatch):
    """DATABASE_URL is honoured when SAGEWAI_DATABASE_URL is absent."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://alias:alias@host/db")
    assert factory.resolve_database_url() == "postgresql://alias:alias@host/db"


def test_sagewai_database_url_wins_over_alias(monkeypatch):
    """SAGEWAI_DATABASE_URL takes precedence over DATABASE_URL."""
    monkeypatch.setenv("SAGEWAI_DATABASE_URL", "postgresql://primary:pw@h/db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://alias:pw@h/db")
    assert factory.resolve_database_url() == "postgresql://primary:pw@h/db"


# ---------------------------------------------------------------------------
# FIX 2 — get_workflow_store() is async and returns an initialized store
# ---------------------------------------------------------------------------

def test_get_engine_is_cached():
    assert factory.get_engine() is factory.get_engine()


def test_is_sqlite_true_by_default():
    assert factory.is_sqlite() is True


@pytest.mark.asyncio
async def test_ensure_schema_creates_sqlite_tables():
    from sqlalchemy import inspect
    engine = factory.get_engine()
    await factory.ensure_schema()
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert "agent_runs" in tables and "workflow_runs" in tables


@pytest.mark.asyncio
async def test_dispose_engine_clears_cache():
    e1 = factory.get_engine()
    await factory.dispose_engine()
    e2 = factory.get_engine()
    assert e1 is not e2


@pytest.mark.asyncio
async def test_get_workflow_store_sqlite_is_initialized():
    """get_workflow_store() returns an initialized SqliteWorkflowStore."""
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore

    store = await factory.get_workflow_store()
    assert isinstance(store, SqliteWorkflowStore)
    # Verify the store is usable — save + load round-trip
    from sagewai.core.state import WorkflowRun
    run = WorkflowRun(workflow_name="test-wf", run_id="run-sqlite-init")
    await store.save_run(run)
    loaded = await store.load_run("test-wf", "run-sqlite-init")
    assert loaded is not None
    assert loaded.run_id == "run-sqlite-init"


@pytest.mark.asyncio
@pytest.mark.skipif(not _PG_URL, reason="SAGEWAI_TEST_DATABASE_URL not set")
async def test_get_workflow_store_postgres_is_initialized(monkeypatch):
    """get_workflow_store() returns a PostgresStore with a live pool for Postgres."""
    from sagewai.core.stores.postgres import PostgresStore
    from sagewai.core.state import WorkflowRun
    from sagewai.db.engine import create_engine as _create_engine
    from sagewai.db.models import Base

    monkeypatch.setenv("SAGEWAI_DATABASE_URL", _PG_URL)
    # Reset engine so it picks up the new env var
    factory.reset_engine()

    # Bootstrap the schema via SQLAlchemy (Alembic not available in tests).
    # The parity conftest does the same for dialect_engine.
    schema_engine = _create_engine(_PG_URL)
    async with schema_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await schema_engine.dispose()

    store = None
    try:
        store = await factory.get_workflow_store()
        assert isinstance(store, PostgresStore)
        # _pool must not be None — the store must be ready
        assert store._pool is not None, "PostgresStore._pool was None after initialize()"

        # Verify the pool is live by executing a trivial query.
        result = await store._pool.fetchval("SELECT 1")
        assert result == 1, "asyncpg pool could not execute a query"

        # Confirm load_run returns None gracefully for a non-existent run
        # (proves the pool can reach the workflow_runs table without crashing).
        loaded = await store.load_run("nonexistent-wf", "nonexistent-run")
        assert loaded is None
    finally:
        if store is not None:
            await store.close()
        factory.reset_engine()
