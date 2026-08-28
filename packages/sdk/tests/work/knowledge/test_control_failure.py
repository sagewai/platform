# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Durable knowledge derived from control failures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import sagewai.work.store as work_store_module
from sagewai.work import TaskCapsuleCompiler, WorkContract, WorkItem, WorkStore
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.knowledge.control_failure import control_failure_finding
from sagewai.work.knowledge.models import KnowledgeItem, KnowledgeKind, KnowledgeQuery
from sagewai.work.knowledge.store import KnowledgeStore
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store(dialect_engine) -> KnowledgeStore:  # noqa: F811
    result = KnowledgeStore(engine=dialect_engine)
    await result.init()
    return result


def _degraded_event() -> WorkEvent:
    return WorkEvent(
        id="event-control-degraded-1",
        project_id="project-a",
        work_id="work-1",
        sequence=4,
        event_type=WorkEventType.CONTROL_DEGRADED,
        actor_type="controller",
        actor_ref="delivery_control",
        payload_json={
            "failed_preconditions": ["observability_fresh", "rollback_ready"],
            "evidence_refs": [
                "metrics://quartz-api/2026-08-28T12:00:00Z",
                "artifact://rollback/quartz-api",
            ],
            "details": (
                "observability_fresh: Quartz API metrics are stale; "
                "rollback_ready: rollback artifact is missing"
            ),
        },
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_control_event_atomically_publishes_one_reusable_project_finding(
    store: KnowledgeStore,
    dialect_engine,  # noqa: F811
) -> None:
    event = _degraded_event()
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()

    await work_store.append_event(event)
    first = await store.get(
        "event-control-degraded-1:control-failure",
        project_id="project-a",
    )
    assert first is not None
    assert first.project_id == "project-a"
    assert first.work_id is None
    assert first.kind is KnowledgeKind.FINDING
    assert first.importance_score > 50
    assert "observability_fresh" in first.statement
    assert "rollback_ready" in first.statement
    assert first.source_refs == (
        "work-event://event-control-degraded-1",
        "metrics://quartz-api/2026-08-28T12:00:00Z",
        "artifact://rollback/quartz-api",
    )
    findings = await store.search(
        KnowledgeQuery(text="Quartz metrics stale", project_id="project-a")
    )
    assert len(findings) == 1
    assert findings == [first]

    await store.publish(
        KnowledgeItem(
            id="unrelated-control-finding",
            project_id="project-a",
            work_id=None,
            kind=KnowledgeKind.FINDING,
            statement=(
                "Control failure for preconditions github-target during work work-9. "
                "Details: github-target: base sha moved during rollout."
            ),
            source_refs=("work-event://other",),
            factness_score=100,
            importance_score=90,
            created_by="controller",
            created_at=NOW,
        )
    )

    related_work = WorkItem(
        id="work-2",
        project_id="project-a",
        profile="software",
        source="local",
        source_ref=None,
        title="Improve Quartz API metrics freshness",
        description="Keep Quartz API metrics current during rollout",
        created_at=NOW,
    )
    contract = WorkContract(
        id="contract-2",
        project_id="project-a",
        work_id=related_work.id,
        version=1,
        goal=related_work.description,
        allowed_scope=("packages/sdk",),
        acceptance_criteria=("Quartz metrics remain fresh",),
        constraints=(),
        non_goals=(),
        evidence_refs=(),
        assumption_ids=(),
        risk="medium",
        design_required=False,
    )
    production_search_text = f"{related_work.title} {related_work.description}"
    assert (
        await store.search(KnowledgeQuery(text=production_search_text, project_id="project-a"))
        == []
    )

    capsule = await TaskCapsuleCompiler(knowledge_store=store).compile(
        work_item=related_work,
        contract=contract,
        stage="analysis",
        search_text=production_search_text,
    )

    assert capsule.knowledge_items == (first,)


@pytest.mark.asyncio
async def test_control_failure_fallback_is_newest_first_and_bounded(
    store: KnowledgeStore,
) -> None:
    for item_id, created_at in (
        ("older-quartz-finding", NOW),
        ("newer-quartz-finding", NOW.replace(minute=1)),
    ):
        await store.publish(
            KnowledgeItem(
                id=item_id,
                project_id="project-a",
                work_id=None,
                kind=KnowledgeKind.FINDING,
                statement="Quartz metrics became stale during deployment",
                source_refs=(f"work-event://{item_id}",),
                factness_score=100,
                importance_score=90,
                created_by="controller",
                created_at=created_at,
            )
        )

    matches = await store.search_high_importance_project_findings_any_term(
        KnowledgeQuery(
            text="Improve Quartz metrics freshness",
            project_id="project-a",
        ),
        limit=1,
    )

    assert [item.id for item in matches] == ["newer-quartz-finding"]


@pytest.mark.asyncio
async def test_control_event_rolls_back_when_finding_insert_fails(
    store: KnowledgeStore,
    dialect_engine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _degraded_event()
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()

    async def fail_insert(*args, **kwargs) -> None:
        raise RuntimeError("injected knowledge insert failure")

    monkeypatch.setattr(work_store_module, "insert_knowledge_item", fail_insert)

    with pytest.raises(RuntimeError, match="injected knowledge insert failure"):
        await work_store.append_event(event)

    assert await work_store.read_events("work-1", project_id="project-a") == []
    assert (
        await store.get(
            "event-control-degraded-1:control-failure",
            project_id="project-a",
        )
        is None
    )


def test_finding_requires_a_project_scoped_control_failure() -> None:
    event = _degraded_event()

    with pytest.raises(ValueError, match="CONTROL_DEGRADED"):
        control_failure_finding(
            event.model_copy(update={"event_type": WorkEventType.CONTROL_RESTORED})
        )

    with pytest.raises(ValueError, match="project-scoped"):
        control_failure_finding(event.model_copy(update={"project_id": None}))
