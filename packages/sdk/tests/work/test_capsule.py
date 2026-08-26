# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""TaskCapsule compilation from canonical Work and Evidence Board state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sagewai.work import TaskCapsule, WorkContract, WorkItem
from sagewai.work.capsule import TaskCapsuleCompiler
from sagewai.work.knowledge import KnowledgeItem, KnowledgeKind, KnowledgeStore
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _work_item(*, project_id: str | None = "project-a") -> WorkItem:
    return WorkItem(
        id="work-1",
        project_id=project_id,
        profile="software",
        source="local",
        source_ref="repo://source/task.md",
        title="Compile a bounded capsule",
        description="Use canonical quartz findings",
        target_systems=("repository",),
        created_at=NOW,
    )


def _contract(*, evidence_refs: tuple[str, ...] = ()) -> WorkContract:
    return WorkContract(
        id="contract-1",
        project_id="project-a",
        work_id="work-1",
        version=1,
        goal="Compile a bounded capsule",
        allowed_scope=("packages/sdk/sagewai/work",),
        acceptance_criteria=("canonical context is sufficient",),
        constraints=("no chat history",),
        non_goals=(),
        evidence_refs=evidence_refs,
        assumption_ids=("assumption-1",),
        risk="low",
        design_required=False,
        profile_context={"base_sha": "a" * 40},
    )


def _knowledge(
    item_id: str,
    statement: str,
    *,
    project_id: str = "project-a",
    work_id: str | None = "work-1",
    offset: int = 0,
) -> KnowledgeItem:
    return KnowledgeItem(
        id=item_id,
        project_id=project_id,
        work_id=work_id,
        kind=KnowledgeKind.FINDING,
        statement=statement,
        source_refs=(f"source://{item_id}",),
        factness_score=100,
        created_by="operator-a",
        created_at=NOW + timedelta(seconds=offset),
    )


@pytest.fixture
async def knowledge_store(dialect_engine) -> KnowledgeStore:  # noqa: F811
    store = KnowledgeStore(engine=dialect_engine)
    await store.init()
    return store


@pytest.mark.asyncio
async def test_compiler_prioritizes_direct_refs_then_scoped_fts(
    knowledge_store: KnowledgeStore,
) -> None:
    for item in (
        _knowledge("direct", "Direct source-of-truth read-back", offset=3),
        _knowledge("work-fts", "Quartz work finding", offset=1),
        _knowledge("project-fts", "Quartz project finding", work_id=None, offset=2),
        _knowledge("other-project", "Quartz private finding", project_id="project-b"),
    ):
        await knowledge_store.publish(item)

    capsule = await TaskCapsuleCompiler(
        knowledge_store=knowledge_store,
        max_knowledge_items=3,
    ).compile(
        work_item=_work_item(),
        contract=_contract(evidence_refs=("direct", "source://opaque")),
        stage="implement",
        search_text="quartz",
        open_assumption_ids=("assumption-1",),
        prior_result_refs=("result://verified",),
        profile_context={"base_sha": "a" * 40, "current_sha": "b" * 40},
    )

    assert capsule.knowledge_refs == ("direct", "work-fts", "project-fts")
    assert tuple(item.id for item in capsule.knowledge_items) == capsule.knowledge_refs
    assert capsule.work_item.source_ref == "repo://source/task.md"
    assert capsule.contract.evidence_refs == ("direct", "source://opaque")
    assert capsule.open_assumption_ids == ("assumption-1",)
    assert capsule.prior_result_refs == ("result://verified",)
    assert capsule.profile_context == {
        "base_sha": "a" * 40,
        "current_sha": "b" * 40,
    }


@pytest.mark.asyncio
async def test_compiler_builds_fresh_capsule_without_session_history(
    knowledge_store: KnowledgeStore,
) -> None:
    compiler = TaskCapsuleCompiler(knowledge_store=knowledge_store)
    first = await compiler.compile(
        work_item=_work_item(),
        contract=_contract(),
        stage="implement",
        search_text="quartz",
    )
    await knowledge_store.publish(_knowledge("new-finding", "Quartz verified later"))
    second = await compiler.compile(
        work_item=_work_item(),
        contract=_contract(),
        stage="implement",
        search_text="quartz",
    )

    assert first.knowledge_refs == ()
    assert second.knowledge_refs == ("new-finding",)
    assert "session" not in TaskCapsule.model_fields


def test_task_capsule_keeps_profile_context_opaque() -> None:
    capsule = TaskCapsule(
        work_id="work-1",
        project_id="project-a",
        stage="implement",
        work_item=_work_item(),
        contract=_contract(),
        knowledge_refs=(),
        knowledge_items=(),
        open_assumption_ids=(),
        prior_result_refs=(),
        profile_context={"software": {"base_sha": "a" * 40}},
    )
    assert TaskCapsule.model_validate_json(capsule.model_dump_json()) == capsule
