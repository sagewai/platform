# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Data models for the intelligence layer — extraction, classification, and more."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractionResult(BaseModel):
    """A named entity extracted from text.

    Attributes:
        text: The entity text as it appears in the source.
        label: Entity type (e.g., ``"PERSON"``, ``"ORG"``, ``"TECHNOLOGY"``).
        start: Character offset start in the source text.
        end: Character offset end in the source text.
        confidence: Extraction confidence score (0.0--1.0).
    """

    text: str
    label: str
    start: int
    end: int
    confidence: float = Field(ge=0.0, le=1.0)


class RelationTriple(BaseModel):
    """A subject-predicate-object triple extracted from text.

    Attributes:
        subject: The source entity.
        predicate: The relationship label.
        object: The target entity.
        confidence: Confidence score (0.0--1.0).
        source_text: The sentence this triple was extracted from.
    """

    subject: str
    predicate: str
    object: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_text: str = ""


class ExtractedFact(BaseModel):
    """A structured fact extracted from conversation or text.

    Attributes:
        content: The fact text.
        fact_type: Category — ``"decision"``, ``"preference"``, ``"entity"``,
            ``"event"``, ``"action"``, or ``"general"``.
        confidence: Confidence score (0.0--1.0).
        entities: Related entity names found in the fact.
    """

    content: str
    fact_type: str = "general"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    entities: list[str] = Field(default_factory=list)
