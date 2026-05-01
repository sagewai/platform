# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for GlobalMemory — cross-agent shared memory."""

from __future__ import annotations

import asyncio

import pytest

from sagewai.memory import GlobalMemory


@pytest.fixture(autouse=True)
def _reset_global_memory():
    """Each test gets a fresh registry of scopes."""
    GlobalMemory.reset()
    yield
    GlobalMemory.reset()


# ── basic factory + singleton-per-scope ────────────────────────────


def test_get_returns_same_instance_for_same_scope():
    a1 = GlobalMemory.get(scope="team-x")
    a2 = GlobalMemory.get(scope="team-x")
    assert a1 is a2


def test_get_returns_different_instances_for_different_scopes():
    a = GlobalMemory.get(scope="team-x")
    b = GlobalMemory.get(scope="team-y")
    assert a is not b
    assert a.scope == "team-x"
    assert b.scope == "team-y"


def test_default_scope_works():
    g = GlobalMemory.get()
    assert g.scope == "default"


def test_empty_scope_raises():
    with pytest.raises(ValueError):
        GlobalMemory(scope="")


# ── cross-agent shared memory (the headline feature) ──────────────


@pytest.mark.asyncio
async def test_two_agents_share_memory_in_same_scope():
    """Two 'agents' adding to the same scope each see each other's writes."""
    team = GlobalMemory.get(scope="acme")

    # Agent 1 adds a fact
    await team.add("Customer ACME-CORP escalated twice last month.")

    # Agent 2 adds a different fact via the SAME scope
    team_b = GlobalMemory.get(scope="acme")  # same instance
    await team_b.add("Our triage runbook is at runbooks/triage.md")

    # Either agent retrieves both
    notes = await team.retrieve("ACME", top_k=5)
    assert any("ACME-CORP" in n for n in notes), f"agent 1 missing ACME fact: {notes}"

    runbook = await team_b.retrieve("triage", top_k=5)
    assert any("triage" in n.lower() for n in runbook), (
        f"agent 2 missing triage fact: {runbook}"
    )


@pytest.mark.asyncio
async def test_scopes_isolated_no_cross_leak():
    """Two scopes never share memory, even if same content is added."""
    team_a = GlobalMemory.get(scope="acme")
    team_b = GlobalMemory.get(scope="globex")

    await team_a.add("Acme secret: alpha-12345")
    await team_b.add("Globex secret: beta-67890")

    # Acme team can't see Globex's secret
    a_results = await team_a.retrieve("beta-67890", top_k=5)
    assert all("beta-67890" not in r for r in a_results), (
        f"acme team leaked globex secret: {a_results}"
    )

    # Globex team can't see Acme's secret
    b_results = await team_b.retrieve("alpha-12345", top_k=5)
    assert all("alpha-12345" not in r for r in b_results), (
        f"globex team leaked acme secret: {b_results}"
    )


@pytest.mark.asyncio
async def test_concurrent_adds_serialise_correctly():
    """50 concurrent adds from many 'agents' all land in the shared memory."""
    team = GlobalMemory.get(scope="busy-team")

    async def add_one(i: int) -> None:
        await team.add(f"Observation #{i}: agents are productive.")

    await asyncio.gather(*(add_one(i) for i in range(50)))

    # Verify the lock counter recorded all 50
    stats = team.stats()
    assert stats["add_count"] == 50, f"expected 50 adds, got {stats['add_count']}"

    # And retrieval works after concurrent contention
    results = await team.retrieve("Observation", top_k=10)
    assert len(results) > 0, "no observations retrievable after concurrent adds"


@pytest.mark.asyncio
async def test_concurrent_retrieves_dont_take_lock():
    """Reads are lock-free; many concurrent retrieves succeed in parallel."""
    team = GlobalMemory.get(scope="reader-test")
    await team.add("Sagewai is the autonomous agent platform.")
    await team.add("Curator records production runs.")

    async def retrieve_one() -> list[str]:
        return await team.retrieve("Sagewai", top_k=2)

    results = await asyncio.gather(*(retrieve_one() for _ in range(20)))
    # All 20 returned non-empty (each saw at least the corpus)
    assert all(len(r) > 0 for r in results)


# ── lifecycle — clear, reset, list_scopes ─────────────────────────


@pytest.mark.asyncio
async def test_clear_drops_content_keeps_scope():
    team = GlobalMemory.get(scope="clearable")
    await team.add("ephemeral fact")
    assert "ephemeral fact" in (await team.retrieve("ephemeral"))

    await team.clear()
    assert team.stats()["add_count"] == 0
    assert "clearable" in GlobalMemory.list_scopes(), (
        "clear() should NOT drop the scope itself"
    )


def test_reset_drops_specific_scope():
    GlobalMemory.get(scope="alpha")
    GlobalMemory.get(scope="beta")
    GlobalMemory.reset(scope="alpha")
    assert "alpha" not in GlobalMemory.list_scopes()
    assert "beta" in GlobalMemory.list_scopes()


def test_reset_with_no_arg_drops_all():
    GlobalMemory.get(scope="x")
    GlobalMemory.get(scope="y")
    GlobalMemory.reset()
    assert GlobalMemory.list_scopes() == []


# ── observability ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_track_counters():
    team = GlobalMemory.get(scope="counted")
    s0 = team.stats()
    assert s0["scope"] == "counted"
    assert s0["add_count"] == 0
    assert s0["retrieve_count"] == 0
    assert s0["age_seconds"] >= 0

    await team.add("fact 1")
    await team.add("fact 2")
    await team.retrieve("fact")
    await team.retrieve("fact")
    await team.retrieve("fact")

    s1 = team.stats()
    assert s1["add_count"] == 2
    assert s1["retrieve_count"] == 3


# ── backend abstraction ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backend_count_returns_authoritative_count():
    """backend_count() reads from the backend, not the per-instance counter."""
    team = GlobalMemory.get(scope="counted-by-backend")
    assert await team.backend_count() == 0

    await team.add("alpha")
    await team.add("beta")
    await team.add("gamma")
    assert await team.backend_count() == 3


@pytest.mark.asyncio
async def test_configure_backend_swaps_storage():
    """Setting a different backend routes future calls through it."""
    from sagewai.memory.global_memory_backends import InMemoryBackend

    initial_backend = GlobalMemory.get_backend()
    assert isinstance(initial_backend, InMemoryBackend)

    fresh_backend = InMemoryBackend()
    GlobalMemory.configure_backend(fresh_backend)
    try:
        assert GlobalMemory.get_backend() is fresh_backend
        team = GlobalMemory.get(scope="post-swap")
        await team.add("from new backend")
        results = await team.retrieve("backend", top_k=5)
        assert len(results) >= 1
    finally:
        GlobalMemory.configure_backend(initial_backend)


def test_postgres_backend_constructor_signature():
    """PostgresBackend can be constructed with a mock pool (no DB required)."""
    from unittest.mock import MagicMock
    from sagewai.memory.global_memory_backends import PostgresBackend

    fake_pool = MagicMock()
    backend = PostgresBackend(connection_pool=fake_pool)
    assert backend._pool is fake_pool


def test_redis_backend_constructor_signature():
    """RedisBackend can be constructed with a mock client (no Redis required)."""
    from unittest.mock import MagicMock
    from sagewai.memory.global_memory_backends import RedisBackend

    fake_redis = MagicMock()
    backend = RedisBackend(redis_client=fake_redis, key_prefix="test")
    assert backend._redis is fake_redis
    assert backend._key("acme") == "test:acme"


@pytest.mark.asyncio
async def test_ensure_backend_ready_is_safe_for_inmemory():
    """ensure_backend_ready() is a no-op for backends without ensure_schema."""
    await GlobalMemory.ensure_backend_ready()
