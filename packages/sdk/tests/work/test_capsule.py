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
from pathlib import Path

import pytest

from sagewai.artifacts import LocalArtifactStore
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
    artifact_refs: tuple[str, ...] = (),
) -> KnowledgeItem:
    return KnowledgeItem(
        id=item_id,
        project_id=project_id,
        work_id=work_id,
        kind=KnowledgeKind.FINDING,
        statement=statement,
        source_refs=(f"source://{item_id}",),
        artifact_refs=artifact_refs,
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
    assert capsule.knowledge_items_considered == 3
    assert capsule.artifact_bytes_referenced == 0


@pytest.mark.asyncio
async def test_compiler_deduplicates_considered_items_and_artifact_bytes(
    knowledge_store: KnowledgeStore,
    tmp_path: Path,
) -> None:
    artifact_store = LocalArtifactStore(root=tmp_path / "objects")
    evidence = artifact_store.put_bytes(
        b"verified evidence",
        media_type="text/plain",
        created_by="test",
    )
    diff = artifact_store.put_bytes(
        b"workspace diff",
        media_type="text/x-diff",
        created_by="test",
    )
    direct = _knowledge(
        "direct",
        "Quartz direct finding",
        artifact_refs=(evidence.storage_ref, "external://opaque"),
    )
    other_work = _knowledge(
        "other-work",
        "Quartz finding from another Work",
        work_id="work-2",
    )
    for item in (direct, other_work):
        await knowledge_store.publish(item)

    capsule = await TaskCapsuleCompiler(
        knowledge_store=knowledge_store,
        artifact_store=artifact_store,
        max_knowledge_items=2,
    ).compile(
        work_item=_work_item(),
        contract=_contract(evidence_refs=(direct.id, direct.id)),
        stage="review",
        search_text="quartz",
        prior_result_refs=(direct.id,),
        referenced_artifacts=(evidence, diff, evidence),
    )

    assert capsule.knowledge_refs == (direct.id,)
    assert capsule.knowledge_items_considered == 2
    assert capsule.artifact_bytes_referenced == evidence.size_bytes + diff.size_bytes


@pytest.mark.asyncio
async def test_compiler_requires_referenced_local_artifact_to_exist(
    knowledge_store: KnowledgeStore,
    tmp_path: Path,
) -> None:
    item = _knowledge(
        "missing-artifact",
        "Quartz artifact is missing",
        artifact_refs=("artifact://sha256:" + "0" * 64,),
    )
    await knowledge_store.publish(item)

    with pytest.raises(FileNotFoundError):
        await TaskCapsuleCompiler(
            knowledge_store=knowledge_store,
            artifact_store=LocalArtifactStore(root=tmp_path / "objects"),
        ).compile(
            work_item=_work_item(),
            contract=_contract(evidence_refs=(item.id,)),
            stage="review",
        )


@pytest.mark.asyncio
async def test_compiler_bounds_fallback_to_remaining_capacity(
    knowledge_store: KnowledgeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct = _knowledge("direct", "Direct source-of-truth read-back")
    fallback = _knowledge(
        "fallback",
        "Quartz metrics became stale",
        work_id=None,
    ).model_copy(update={"importance_score": 90})
    await knowledge_store.publish(direct)
    await knowledge_store.publish(fallback)

    requested_limits: list[int] = []
    original = knowledge_store.search_high_importance_project_findings_any_term

    async def track_limit(query, *, limit: int):
        requested_limits.append(limit)
        return await original(query, limit=limit)

    monkeypatch.setattr(
        knowledge_store,
        "search_high_importance_project_findings_any_term",
        track_limit,
    )

    capsule = await TaskCapsuleCompiler(
        knowledge_store=knowledge_store,
        max_knowledge_items=2,
    ).compile(
        work_item=_work_item(),
        contract=_contract(evidence_refs=(direct.id,)),
        stage="implement",
        search_text="Quartz metrics freshness",
    )

    assert requested_limits == [1]
    assert capsule.knowledge_refs == (direct.id, fallback.id)


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


@pytest.mark.asyncio
async def test_compiler_resolves_prior_result_knowledge_for_fresh_stage(
    knowledge_store: KnowledgeStore,
) -> None:
    prior = _knowledge("verification-1", "Verification command passed")
    await knowledge_store.publish(prior)

    capsule = await TaskCapsuleCompiler(knowledge_store=knowledge_store).compile(
        work_item=_work_item(),
        contract=_contract(),
        stage="review",
        prior_result_refs=(prior.id, "command://opaque"),
    )

    assert capsule.knowledge_refs == (prior.id,)
    assert capsule.knowledge_items == (prior,)
    assert capsule.prior_result_refs == (prior.id, "command://opaque")


def test_task_capsule_keeps_profile_context_opaque() -> None:
    capsule = TaskCapsule(
        work_id="work-1",
        project_id="project-a",
        stage="implement",
        work_item=_work_item(),
        contract=_contract(),
        knowledge_refs=(),
        knowledge_items=(),
        knowledge_items_considered=0,
        artifact_bytes_referenced=0,
        open_assumption_ids=(),
        prior_result_refs=(),
        profile_context={"software": {"base_sha": "a" * 40}},
    )
    assert TaskCapsule.model_validate_json(capsule.model_dump_json()) == capsule
