# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sqlite-vec default-wiring (Task PR3.3).

All tests that touch the filesystem use the ``_home`` fixture, which
redirects SAGEWAI_HOME to a temporary directory so no real ~/.sagewai
writes occur.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))


def test_sqlite_vec_available_here():
    from sagewai.memory.sqlite_vec import sqlite_vec_available

    assert sqlite_vec_available() is True  # verified loadable on this platform


@pytest.mark.asyncio
async def test_sqlite_vec_backend_durable_per_scope(_home):
    from sagewai.memory.global_memory_backends import SqliteVecBackend

    b = SqliteVecBackend()
    await b.add("team-x", "ACME escalated twice")
    out = await b.retrieve("team-x", "ACME", top_k=5)
    assert any("ACME" in r for r in out)
    # scope isolation — team-y never written, must return empty
    assert await b.retrieve("team-y", "ACME", top_k=5) == []


@pytest.mark.asyncio
async def test_ragengine_default_is_vectormemory_when_unconfigured(_home):
    # safety: no configured default → in-process VectorMemory (no file writes)
    from sagewai.memory.rag import RAGEngine, configure_default_vector_memory
    from sagewai.memory.vector import VectorMemory

    configure_default_vector_memory(None)
    rag = RAGEngine()
    assert isinstance(rag.vector, VectorMemory)


@pytest.mark.asyncio
async def test_ragengine_uses_configured_default(_home):
    from sagewai.memory.rag import RAGEngine, configure_default_vector_memory
    from sagewai.memory.sqlite_vec import SqliteVecMemory

    configure_default_vector_memory(lambda pid: SqliteVecMemory(project_id=pid))
    try:
        rag = RAGEngine(project_id="p1")
        assert isinstance(rag.vector, SqliteVecMemory)
    finally:
        configure_default_vector_memory(None)


def test_fallback_when_extension_unavailable(monkeypatch, _home):
    import sagewai.memory.sqlite_vec as sv

    monkeypatch.setattr(sv, "_EXT_AVAILABLE", False)
    assert sv.sqlite_vec_available() is False
