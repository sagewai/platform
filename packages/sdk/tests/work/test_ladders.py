# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Runtime ladders: position selection and reviewer independence."""

from __future__ import annotations

from pathlib import Path

import pytest

from sagewai.core.state import InMemoryStore
from sagewai.work import CapabilitySet, WorkStore
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.profiles.software import SoftwareStageOperator, StageOperatorLadder
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.test_lifecycle import (
    MutationRuntime,
    ReviewRuntime,
    _lifecycle,
)


class _Runtime:
    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, request, capsule, capabilities, workspace):  # pragma: no cover - never called
        raise AssertionError


def _operator(actor: str, runtime: str) -> SoftwareStageOperator:
    return SoftwareStageOperator(
        actor_ref=actor,
        runtime=_Runtime(runtime),
        capabilities=CapabilitySet(project_id="p", grants=()),
        controller=object(),
    )


def test_ladder_positions_clamp_to_the_last_operator() -> None:
    ladder = StageOperatorLadder((_operator("a", "harness"), _operator("b", "codex")))
    assert ladder.for_position(1).runtime.name == "harness"
    assert ladder.for_position(2).runtime.name == "codex"
    assert ladder.for_position(9).runtime.name == "codex"
    assert len(ladder) == 2
    assert ladder.actor_refs == ("a", "b")
    with pytest.raises(ValueError):
        ladder.for_position(0)
    with pytest.raises(ValueError):
        StageOperatorLadder(())


def test_reviewer_cannot_share_an_actor_with_any_implementer_position(
    dialect_engine,  # noqa: F811
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="reviewer cannot review its own result"):
        _lifecycle(
            repository=tmp_path / "repository",
            worktree_root=tmp_path / "worktrees",
            work_store=WorkStore(engine=dialect_engine),
            knowledge_store=KnowledgeStore(engine=dialect_engine),
            durability=InMemoryStore(),
            implementer=MutationRuntime(implement_text="initial", repair_text="fixed"),
            implementer_ladder=(
                MutationRuntime(implement_text="initial", repair_text="fixed"),
                MutationRuntime(implement_text="initial", repair_text="fixed"),
            ),
            reviewer=ReviewRuntime("accept"),
            repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
            commands=("true",),
            reviewer_actor="operator:implementer:2",
        )


def test_max_attempts_per_stage_must_be_positive(
    dialect_engine,  # noqa: F811
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="max_attempts_per_stage"):
        _lifecycle(
            repository=tmp_path / "repository",
            worktree_root=tmp_path / "worktrees",
            work_store=WorkStore(engine=dialect_engine),
            knowledge_store=KnowledgeStore(engine=dialect_engine),
            durability=InMemoryStore(),
            implementer=MutationRuntime(implement_text="initial", repair_text="fixed"),
            reviewer=ReviewRuntime("accept"),
            repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
            commands=("true",),
            max_attempts_per_stage=0,
        )
