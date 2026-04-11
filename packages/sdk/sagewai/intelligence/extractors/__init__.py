# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Extractors — entity, relation, and fact extraction backends."""

from sagewai.intelligence.extractors.gliner_extractor import (
    GLiNEREntityExtractor,
    HeuristicRelationExtractor,
)
from sagewai.intelligence.extractors.hybrid_fact_extractor import (
    HybridFactExtractor,
)
from sagewai.intelligence.extractors.llm_extractor import (
    LLMEntityExtractor,
    LLMRelationExtractor,
)
from sagewai.intelligence.extractors.llm_fact_extractor import LLMFactExtractor
from sagewai.intelligence.extractors.protocol import (
    EntityExtractor,
    FactExtractor,
    RelationExtractor,
)
from sagewai.intelligence.extractors.rule_based import RuleBasedFactExtractor

__all__ = [
    "EntityExtractor",
    "FactExtractor",
    "GLiNEREntityExtractor",
    "HeuristicRelationExtractor",
    "HybridFactExtractor",
    "LLMEntityExtractor",
    "LLMFactExtractor",
    "LLMRelationExtractor",
    "RelationExtractor",
    "RuleBasedFactExtractor",
]
