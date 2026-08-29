# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Canonical models for the minimal shared Evidence Board."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KnowledgeKind(str, Enum):
    """Small typed vocabulary for durable shared knowledge."""

    FACT = "fact"
    FINDING = "finding"
    INFERENCE = "inference"
    DECISION = "decision"
    QUESTION = "question"
    ARTIFACT = "artifact"
    ACTION_RESULT = "action_result"
    CONTRADICTION = "contradiction"


class KnowledgeItem(BaseModel):
    """One immutable knowledge or evidence record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str | None
    work_id: str | None
    kind: KnowledgeKind
    statement: str
    source_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    factness_score: Literal[0, 100] = 0
    importance_score: int = 50
    created_by: str
    created_at: datetime
    supersedes: str | None = None

    @model_validator(mode="after")
    def require_evidence_for_full_factness(self) -> KnowledgeItem:
        if self.factness_score == 100 and not (self.source_refs or self.artifact_refs):
            raise ValueError(
                "factness_score 100 requires at least one source or artifact reference"
            )
        return self


class KnowledgeQuery(BaseModel):
    """Project-scoped full-text query with optional WorkItem narrowing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, pattern=r"\S")
    project_id: str | None
    work_id: str | None = None
