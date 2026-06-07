# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Integration tests: SQLite is the zero-config default backend.

Verifies that with no SAGEWAI_DATABASE_URL set:
- resolve_database_url() returns a sqlite+aiosqlite:// URL
- is_sqlite() returns True
- PostgresAnalyticsStore persists cost records across a simulated restart
  (dispose + reset the engine, open a new store on the same file)

These tests never set SAGEWAI_DATABASE_URL, so they run in the default
SQLite path. The fixture resets the factory engine before and after to
avoid polluting other tests.
"""

from __future__ import annotations

import pytest

from sagewai.db import factory


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Redirect SAGEWAI_HOME to a clean tmp dir and clear DATABASE_URL."""
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SAGEWAI_DATABASE_URL", raising=False)
    factory.reset_engine()
    yield
    factory.reset_engine()


@pytest.mark.asyncio
async def test_default_backend_is_sqlite():
    """With no DATABASE_URL the resolved URL is a sqlite+aiosqlite:// URL."""
    url = factory.resolve_database_url()
    assert url.startswith("sqlite+aiosqlite:///"), (
        f"Expected sqlite+aiosqlite URL, got: {url}"
    )


@pytest.mark.asyncio
async def test_is_sqlite_returns_true():
    """is_sqlite() must be True in the default (no DATABASE_URL) config."""
    assert factory.is_sqlite() is True


@pytest.mark.asyncio
async def test_analytics_persists_across_restart():
    """Cost records written to Store-1 survive an engine dispose + reset.

    Simulates the process restoring from disk (e.g. admin server restart).
    """
    from sagewai.admin.postgres_analytics import PostgresAnalyticsStore

    await factory.ensure_schema()

    # Write via store-1
    s1 = PostgresAnalyticsStore(engine=factory.get_engine())
    await s1.record_cost("agent-a", "gpt-4o", 1.5, 1000)

    # Simulate restart: dispose the connection pool and drop the reference
    await factory.dispose_engine()
    factory.reset_engine()

    # Read via store-2 (same file, new engine)
    s2 = PostgresAnalyticsStore(engine=factory.get_engine())
    costs = await s2.get_costs()
    assert costs["total_cost_usd"] >= 1.5, (
        f"Expected total_cost_usd >= 1.5 after restart, got: {costs}"
    )
    assert costs["record_count"] >= 1, (
        f"Expected at least 1 record after restart, got: {costs}"
    )


@pytest.mark.asyncio
async def test_analytics_multiple_agents_persist():
    """Multiple cost records from different agents survive a simulated restart."""
    from sagewai.admin.postgres_analytics import PostgresAnalyticsStore

    await factory.ensure_schema()

    s1 = PostgresAnalyticsStore(engine=factory.get_engine())
    await s1.record_cost("agent-a", "gpt-4o", 0.5, 100)
    await s1.record_cost("agent-b", "claude-3", 0.8, 200)

    # Simulate restart
    await factory.dispose_engine()
    factory.reset_engine()

    s2 = PostgresAnalyticsStore(engine=factory.get_engine())
    costs = await s2.get_costs()
    assert costs["record_count"] >= 2
    assert costs["total_cost_usd"] >= 1.3


@pytest.mark.asyncio
async def test_workflow_store_is_sqlite_backed():
    """get_workflow_store() returns an initialized SqliteWorkflowStore when no DATABASE_URL is set."""
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore

    store = await factory.get_workflow_store()
    assert isinstance(store, SqliteWorkflowStore), (
        f"Expected SqliteWorkflowStore, got: {type(store).__name__}"
    )


@pytest.mark.asyncio
async def test_configure_default_workflow_store_does_not_affect_explicit():
    """DurableWorkflow with explicit store= ignores _default_store."""
    from sagewai.core.state import (
        DurableWorkflow,
        InMemoryStore,
        configure_default_workflow_store,
    )
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore

    await factory.ensure_schema()
    sqlite_store = SqliteWorkflowStore(factory.get_engine())

    # Set the default to the SQLite store
    configure_default_workflow_store(sqlite_store)
    try:
        # Explicit store= takes precedence
        explicit_store = InMemoryStore()
        wf = DurableWorkflow(name="test-explicit", store=explicit_store)
        assert wf._store is explicit_store, (
            "DurableWorkflow should use the explicitly supplied store"
        )
        assert not isinstance(wf._store, SqliteWorkflowStore)
    finally:
        # Reset so other tests are not affected
        configure_default_workflow_store(None)


@pytest.mark.asyncio
async def test_configure_default_workflow_store_applies_when_no_store():
    """DurableWorkflow with no store= picks up _default_store."""
    from sagewai.core.state import DurableWorkflow, configure_default_workflow_store
    from sagewai.core.stores.sqlite_store import SqliteWorkflowStore

    await factory.ensure_schema()
    sqlite_store = SqliteWorkflowStore(factory.get_engine())

    configure_default_workflow_store(sqlite_store)
    try:
        wf = DurableWorkflow(name="test-default")
        assert wf._store is sqlite_store, (
            "DurableWorkflow should pick up the configured default store"
        )
    finally:
        configure_default_workflow_store(None)


@pytest.mark.asyncio
async def test_no_default_workflow_store_falls_back_to_in_memory():
    """When no default is configured, DurableWorkflow uses InMemoryStore."""
    from sagewai.core.state import (
        DurableWorkflow,
        InMemoryStore,
        configure_default_workflow_store,
    )

    # Ensure no global default is set (reset to None)
    configure_default_workflow_store(None)
    wf = DurableWorkflow(name="test-fallback")
    assert isinstance(wf._store, InMemoryStore), (
        f"Expected InMemoryStore fallback, got: {type(wf._store).__name__}"
    )
