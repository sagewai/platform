# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""NebulaGraph integration tests — require docker-compose.dev.yml.

Run: pytest tests/test_integration_nebula.py -v -m integration
"""

import uuid

import pytest

try:
    from nebula3.gclient.net import ConnectionPool as _NebulaPool  # noqa: F401

    _HAS_NEBULA = True
except ImportError:
    _HAS_NEBULA = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _HAS_NEBULA, reason="nebula3-python not installed"),
]


@pytest.fixture
def space_name():
    return f"test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def nebula_memory(space_name):
    from sagewai.memory.nebula import NebulaGraphMemory

    mem = NebulaGraphMemory(
        host="localhost",
        port=9669,
        space=space_name,
        user="root",
        password="nebula",
    )
    yield mem
    mem.clear()


class TestNebulaGraphIntegration:
    @pytest.mark.asyncio
    async def test_add_and_retrieve_relation(self, nebula_memory):
        """Add a relation and retrieve it via keyword query."""
        await nebula_memory.add_relation("Python", "is_a", "Programming Language")
        results = await nebula_memory.retrieve("Python", top_k=5)
        assert len(results) >= 1
        combined = " ".join(results)
        assert "Python" in combined

    @pytest.mark.asyncio
    async def test_get_neighbors(self, nebula_memory):
        """get_neighbors returns connected entities."""
        await nebula_memory.add_relation("Alice", "knows", "Bob")
        await nebula_memory.add_relation("Alice", "knows", "Charlie")
        neighbors = await nebula_memory.get_neighbors("Alice", depth=1)
        assert len(neighbors) >= 2

    @pytest.mark.asyncio
    async def test_delete_entity(self, nebula_memory):
        """delete removes an entity and its relations."""
        await nebula_memory.add_relation("TempEntity", "has", "TempRelation")
        deleted = await nebula_memory.delete("TempEntity")
        assert deleted is True

    @pytest.mark.asyncio
    async def test_store_extracts_relations(self, nebula_memory):
        """store() should extract relations from text and add them."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "sagewai.memory.nebula._extract_relations",
            new_callable=AsyncMock,
            return_value=[("Earth", "orbits", "Sun")],
        ):
            await nebula_memory.store("The Earth orbits the Sun")
            results = await nebula_memory.retrieve("Earth", top_k=5)
            assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_clear_drops_space(self, nebula_memory):
        """clear() should drop the space entirely."""
        await nebula_memory.add_relation("X", "r", "Y")
        await nebula_memory.clear()
        # After clear, space is gone — next operation should reinitialize
        results = await nebula_memory.retrieve("X", top_k=5)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_concurrent_writes(self, nebula_memory):
        """Multiple concurrent writes should not corrupt data."""
        import asyncio

        tasks = [
            nebula_memory.add_relation(f"Entity{i}", "relates_to", f"Target{i}") for i in range(10)
        ]
        await asyncio.gather(*tasks)
        # Verify at least some relations exist
        results = await nebula_memory.retrieve("Entity0", top_k=5)
        assert len(results) >= 1
