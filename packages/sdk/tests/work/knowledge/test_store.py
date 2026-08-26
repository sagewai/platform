# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Dual-dialect tests for the minimal shared Evidence Board."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from sagewai.db.engine import create_engine
from sagewai.db.models import Base
from sagewai.work.knowledge import (
    KnowledgeItem,
    KnowledgeKind,
    KnowledgeQuery,
    KnowledgeStore,
)
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _item(
    item_id: str,
    statement: str,
    *,
    project_id: str = "project-a",
    work_id: str | None = "work-1",
    kind: KnowledgeKind = KnowledgeKind.FINDING,
    source_refs: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    factness_score: int = 0,
) -> KnowledgeItem:
    return KnowledgeItem(
        id=item_id,
        project_id=project_id,
        work_id=work_id,
        kind=kind,
        statement=statement,
        source_refs=source_refs,
        artifact_refs=artifact_refs,
        factness_score=factness_score,
        created_by="operator-a",
        created_at=NOW,
    )


@pytest.fixture
async def store(dialect_engine) -> KnowledgeStore:  # noqa: F811
    result = KnowledgeStore(engine=dialect_engine)
    await result.init()
    return result


@pytest.mark.asyncio
async def test_independent_engine_reads_metadata_bootstrapped_sqlite_board(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'independent.db'}"
    publisher_engine = create_engine(database_url)
    async with publisher_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    publisher = KnowledgeStore(engine=publisher_engine)
    item = _item(
        "knowledge-1",
        "The schema migration was verified.",
        source_refs=("repo://sha/migration.py#L1",),
        artifact_refs=("artifact://sha256:abc123",),
    )

    await publisher.publish(item)
    await publisher_engine.dispose()

    reader_engine = create_engine(database_url)
    reader = KnowledgeStore(engine=reader_engine)
    try:
        matches = await reader.search(
            KnowledgeQuery(
                text="schema migration",
                project_id="project-a",
                work_id="work-1",
            )
        )

        assert matches == [item]
        assert await reader.get(item.id, project_id="project-a") == item
    finally:
        await reader_engine.dispose()


@pytest.mark.asyncio
async def test_publish_is_append_only(store: KnowledgeStore) -> None:
    original = _item("knowledge-1", "Original finding")
    await store.publish(original)

    with pytest.raises(IntegrityError):
        await store.publish(_item("knowledge-1", "Replacement finding"))

    assert await store.get(original.id, project_id="project-a") == original
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")


@pytest.mark.asyncio
async def test_get_is_project_scoped(store: KnowledgeStore) -> None:
    await store.publish(_item("knowledge-1", "Scoped finding"))

    assert await store.get("knowledge-1", project_id="project-b") is None


@pytest.mark.asyncio
async def test_full_text_search_respects_project_and_work_scope(
    store: KnowledgeStore,
) -> None:
    items = [
        _item("a-work-1", "Quartz migration verified", work_id="work-1"),
        _item("a-work-2", "Quartz deployment verified", work_id="work-2"),
        _item("a-project", "Quartz project invariant", work_id=None),
        _item(
            "b-work-1",
            "Quartz migration failed",
            project_id="project-b",
            work_id="work-1",
        ),
    ]
    for item in items:
        await store.publish(item)

    work_results = await store.search(
        KnowledgeQuery(text="Quartz", project_id="project-a", work_id="work-1")
    )
    project_results = await store.search(KnowledgeQuery(text="Quartz", project_id="project-a"))
    other_project_results = await store.search(
        KnowledgeQuery(text="Quartz", project_id="project-b")
    )

    assert [item.id for item in work_results] == ["a-work-1"]
    assert [item.id for item in project_results] == [
        "a-project",
        "a-work-1",
        "a-work-2",
    ]
    assert [item.id for item in other_project_results] == ["b-work-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query_text", "expected_ids"),
    [
        ("read-back", ["literal-evidence"]),
        ("api://system/x", ["literal-evidence"]),
        ("read-", ["literal-evidence"]),
        ('read"back', ["literal-evidence"]),
        ("matched OR failed", []),
    ],
)
async def test_full_text_search_treats_operator_input_as_plaintext(
    store: KnowledgeStore,
    query_text: str,
    expected_ids: list[str],
) -> None:
    await store.publish(
        _item(
            "literal-evidence",
            "Target api://system/x read-back matched xylophone",
        )
    )
    await store.publish(_item("failed-evidence", "Target verification failed"))

    results = await store.search(
        KnowledgeQuery(text=query_text, project_id="project-a", work_id="work-1")
    )

    assert [item.id for item in results] == expected_ids


@pytest.mark.asyncio
async def test_full_text_search_treats_prefix_operator_as_literal_term(
    store: KnowledgeStore,
) -> None:
    await store.publish(_item("exact-token-evidence", "Standalone x marker"))
    await store.publish(_item("prefix-evidence", "Xylophone only"))

    results = await store.search(
        KnowledgeQuery(text="x*", project_id="project-a", work_id="work-1")
    )

    assert [item.id for item in results] == ["exact-token-evidence"]


@pytest.mark.asyncio
async def test_factness_and_refs_round_trip_without_interpretation(
    store: KnowledgeStore,
) -> None:
    item = _item(
        "knowledge-100",
        "The target system read-back matched.",
        source_refs=("api://system/item@version", "command://run/readback"),
        artifact_refs=("artifact://sha256:def456",),
        factness_score=100,
    )

    await store.publish(item)

    assert await store.get(item.id, project_id=item.project_id) == item


@pytest.mark.asyncio
async def test_original_artifact_reference_survives_derived_item(
    store: KnowledgeStore,
) -> None:
    artifact = _item(
        "artifact-item",
        "Raw verification output",
        kind=KnowledgeKind.ARTIFACT,
        artifact_refs=("artifact://sha256:immutable",),
    )
    await store.publish(artifact)
    await store.publish(
        _item(
            "derived-finding",
            "The verification output confirms the migration.",
            source_refs=("knowledge://artifact-item",),
            artifact_refs=artifact.artifact_refs,
            factness_score=100,
        )
    )

    stored = await store.get(artifact.id, project_id=artifact.project_id)
    assert stored == artifact
    assert stored.artifact_refs == ("artifact://sha256:immutable",)
