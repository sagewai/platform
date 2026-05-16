# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for Mission ↔ RAGEngine branch stamping (Plan 1, Task 8)."""

from __future__ import annotations

import pytest

from sagewai.autopilot.mission import Mission
from sagewai.memory import MemoryBranch
from sagewai.memory.rag import RAGEngine, RetrievalStrategy


def _build_mission(*, mission_id: str, memory: RAGEngine) -> Mission:
    """Build a Mission with minimal required arguments for branch-stamping tests."""
    return Mission(
        mission_id=mission_id,
        project_id="proj-test",
        blueprint_id="SYNTHETIC_test",
        blueprint_version="0.1.0",
        slots={},
        memory=memory,
    )


def test_mission_assigns_its_id_as_memory_branch():
    """Mission with a RAGEngine memory must scope the engine to its mission id."""
    rag = RAGEngine(vector=None, graph=None, strategy=RetrievalStrategy.VECTOR_ONLY)
    m = _build_mission(mission_id="m-42", memory=rag)
    assert rag._branch == MemoryBranch(mission_id="m-42")


def test_mission_overrides_default_global_root():
    """The default global_root branch must be replaced, not retained."""
    rag = RAGEngine(vector=None, graph=None, strategy=RetrievalStrategy.HYBRID)
    assert rag._branch == MemoryBranch.global_root()  # precondition
    _build_mission(mission_id="ms-abc123", memory=rag)
    assert rag._branch != MemoryBranch.global_root()
    assert rag._branch == MemoryBranch(mission_id="ms-abc123")


def test_mission_without_memory_does_not_raise():
    """Constructing a Mission without a memory argument must not raise."""
    m = Mission(
        mission_id="ms-nomem",
        project_id="proj-test",
        blueprint_id="SYNTHETIC_test",
        blueprint_version="0.1.0",
        slots={},
    )
    assert m.memory is None


def test_branch_scoped_namespace_reflects_mission_id():
    """After stamping, branch.scoped() must produce mission-prefixed namespaces."""
    rag = RAGEngine(vector=None, graph=None, strategy=RetrievalStrategy.VECTOR_ONLY)
    _build_mission(mission_id="m-99", memory=rag)
    assert rag._branch.scoped("semantic") == "m-99/semantic"
    assert rag._branch.scoped("preferences") == "m-99/preferences"


def test_two_missions_get_independent_branches():
    """Two missions sharing the same RAGEngine would each stamp their own id."""
    rag_a = RAGEngine(vector=None, graph=None, strategy=RetrievalStrategy.VECTOR_ONLY)
    rag_b = RAGEngine(vector=None, graph=None, strategy=RetrievalStrategy.VECTOR_ONLY)
    _build_mission(mission_id="m-1", memory=rag_a)
    _build_mission(mission_id="m-2", memory=rag_b)
    assert rag_a._branch == MemoryBranch(mission_id="m-1")
    assert rag_b._branch == MemoryBranch(mission_id="m-2")
    assert rag_a._branch != rag_b._branch


def test_shared_engine_across_missions_raises():
    """A single RAGEngine cannot be silently shared across missions — would overwrite branches."""
    rag = RAGEngine(vector=None, graph=None, strategy=RetrievalStrategy.VECTOR_ONLY)
    _build_mission(mission_id="m-1", memory=rag)
    with pytest.raises(ValueError, match="already scoped"):
        _build_mission(mission_id="m-2", memory=rag)


def test_re_stamp_with_same_mission_id_is_idempotent():
    """Stamping the same mission_id twice is a no-op (idempotent)."""
    rag = RAGEngine(vector=None, graph=None, strategy=RetrievalStrategy.VECTOR_ONLY)
    _build_mission(mission_id="m-1", memory=rag)
    # Re-stamping with the same id should not raise.
    _build_mission(mission_id="m-1", memory=rag)
    assert rag._branch == MemoryBranch(mission_id="m-1")
