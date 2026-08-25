# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Model tests for the minimal shared Evidence Board."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sagewai.work.knowledge import KnowledgeItem, KnowledgeKind, KnowledgeQuery

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _item(**updates) -> KnowledgeItem:
    values = {
        "id": "knowledge-1",
        "project_id": "project-a",
        "work_id": "work-1",
        "kind": KnowledgeKind.FINDING,
        "statement": "The migration head is 022.",
        "source_refs": ("repo://sha/migration.py#L1",),
        "artifact_refs": ("artifact://sha256:abc123",),
        "created_by": "operator-a",
        "created_at": NOW,
    }
    values.update(updates)
    return KnowledgeItem.model_validate(values)


def test_knowledge_kind_vocabulary_is_exact() -> None:
    assert {kind.value for kind in KnowledgeKind} == {
        "fact",
        "finding",
        "inference",
        "decision",
        "question",
        "artifact",
        "action_result",
        "contradiction",
    }


def test_unsupported_claim_defaults_to_zero_factness() -> None:
    assert _item(source_refs=()).factness_score == 0


def test_direct_source_readback_records_full_factness() -> None:
    item = _item(factness_score=100, source_refs=("api://system/item@version",))

    assert item.factness_score == 100


@pytest.mark.parametrize("score", [1, 50, 99])
def test_intermediate_factness_scores_are_deferred(score: int) -> None:
    with pytest.raises(ValidationError):
        _item(factness_score=score)


def test_knowledge_item_is_immutable_and_refs_round_trip() -> None:
    item = _item()
    rebuilt = KnowledgeItem.model_validate(item.model_dump(mode="json"))

    assert rebuilt == item
    assert rebuilt.source_refs == ("repo://sha/migration.py#L1",)
    assert rebuilt.artifact_refs == ("artifact://sha256:abc123",)
    assert rebuilt.importance_score == 50
    with pytest.raises(ValidationError):
        item.statement = "changed"  # type: ignore[misc]


def test_knowledge_query_requires_project_and_optionally_scopes_work() -> None:
    project_query = KnowledgeQuery(text="migration head", project_id="project-a")
    work_query = KnowledgeQuery(
        text="migration head",
        project_id="project-a",
        work_id="work-1",
    )

    assert project_query.work_id is None
    assert work_query.work_id == "work-1"
