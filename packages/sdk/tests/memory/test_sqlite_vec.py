# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for SqliteVecMemory — sqlite-vec-backed durable vector store."""

from __future__ import annotations

import pytest

from sagewai.memory import MemoryProvider
from sagewai.intelligence.embeddings.hash_embedder import HashEmbedder


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    return tmp_path / "home" / "db" / "sagewai.db"


@pytest.mark.asyncio
async def test_satisfies_memory_provider(db_path):
    from sagewai.memory.sqlite_vec import SqliteVecMemory
    assert isinstance(SqliteVecMemory(db_path=str(db_path), embedder=HashEmbedder(dimension=384)), MemoryProvider)


@pytest.mark.asyncio
async def test_store_and_recall(db_path):
    from sagewai.memory.sqlite_vec import SqliteVecMemory
    mem = SqliteVecMemory(db_path=str(db_path), embedder=HashEmbedder(dimension=384), project_id="p1")
    await mem.store("The capital of France is Paris.")
    await mem.store("Guido created the Python language.")
    await mem.store("The mitochondria is the powerhouse of the cell.")
    out = await mem.retrieve("Who created Python?", top_k=2)
    assert any("Guido" in r or "Python" in r for r in out)


@pytest.mark.asyncio
async def test_persists_across_reopen(db_path):
    from sagewai.memory.sqlite_vec import SqliteVecMemory
    e = HashEmbedder(dimension=384)
    m1 = SqliteVecMemory(db_path=str(db_path), embedder=e, project_id="p1")
    await m1.store("Durable learning entry about widgets.")
    await m1.close()
    m2 = SqliteVecMemory(db_path=str(db_path), embedder=e, project_id="p1")
    out = await m2.retrieve("widgets", top_k=5)
    assert any("widgets" in r.lower() for r in out)


@pytest.mark.asyncio
async def test_project_scoping(db_path):
    from sagewai.memory.sqlite_vec import SqliteVecMemory
    e = HashEmbedder(dimension=384)
    a = SqliteVecMemory(db_path=str(db_path), embedder=e, project_id="A")
    b = SqliteVecMemory(db_path=str(db_path), embedder=e, project_id="B")
    await a.store("secret only in project A")
    assert await b.retrieve("secret", top_k=5) == []
    assert any("secret" in r for r in await a.retrieve("secret", top_k=5))


@pytest.mark.asyncio
async def test_delete(db_path):
    from sagewai.memory.sqlite_vec import SqliteVecMemory
    mem = SqliteVecMemory(db_path=str(db_path), embedder=HashEmbedder(dimension=384), project_id="p1")
    doc_id = await mem.store("deletable entry")
    ok = await mem.delete(doc_id)
    assert ok is True
    # Deleting a nonexistent (or already-deleted) id returns False
    assert await mem.delete(doc_id) is False
    assert await mem.delete("nonexistent-uuid") is False
