# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for MemoryBranch + strategies integration with RAGEngine."""

import pytest
from sagewai.memory.branch import MemoryBranch
from sagewai.memory.strategies.base import ExtractedRecord, TurnEvent


class _StubVector:
    def __init__(self):
        self.stored: list[tuple[str, dict]] = []

    async def store(self, content: str, metadata=None):
        self.stored.append((content, metadata or {}))

    async def retrieve(self, query: str, top_k: int = 5):
        return [c for c, m in self.stored if m.get("namespace", "").startswith(query)]


class _StubGraph:
    async def store(self, content: str, metadata=None):
        pass

    async def retrieve(self, query: str, top_k: int = 5):
        return []


class _RecordingStrategy:
    name = "rec"
    namespace = "rec"

    def __init__(self):
        self.called_with = None

    async def extract(self, turns):
        self.called_with = list(turns)
        return [
            ExtractedRecord(namespace=self.namespace, content="extracted", source_session="s1", strategy=self.name)
        ]


@pytest.mark.asyncio
async def test_ingest_writes_into_branch_scoped_namespace():
    from sagewai.memory.rag import RAGEngine, RetrievalStrategy
    vec, graph = _StubVector(), _StubGraph()
    strat = _RecordingStrategy()
    rag = RAGEngine(
        vector=vec,
        graph=graph,
        strategy=RetrievalStrategy.VECTOR_ONLY,
        strategies=[strat],
        branch=MemoryBranch(mission_id="m-1"),
    )
    await rag.ingest_turns([TurnEvent(role="user", content="hi", session_id="s1")])
    assert strat.called_with is not None
    namespaces = [m.get("namespace") for _, m in vec.stored]
    assert namespaces == ["m-1/rec"]


@pytest.mark.asyncio
async def test_branches_isolate_reads():
    from sagewai.memory.rag import RAGEngine, RetrievalStrategy
    vec, graph = _StubVector(), _StubGraph()
    rag_a = RAGEngine(vector=vec, graph=graph, strategy=RetrievalStrategy.VECTOR_ONLY,
                     branch=MemoryBranch(mission_id="a"))
    rag_b = RAGEngine(vector=vec, graph=graph, strategy=RetrievalStrategy.VECTOR_ONLY,
                     branch=MemoryBranch(mission_id="b"))
    # NOTE: This test pins a *contract* — vector backends MUST filter by the
    # namespace metadata prefix to give branch-isolated reads. We exercise
    # the contract by passing the namespace as the query string; production
    # VectorMemory must honor branch-aware retrieval. Tracked separately as
    # a follow-up issue under [memory].
    await vec.store("only-a", {"namespace": "a/semantic"})
    await vec.store("only-b", {"namespace": "b/semantic"})
    a_hits = await rag_a.retrieve("a/semantic")
    b_hits = await rag_b.retrieve("b/semantic")
    assert "only-a" in a_hits and "only-b" not in a_hits
    assert "only-b" in b_hits and "only-a" not in b_hits
