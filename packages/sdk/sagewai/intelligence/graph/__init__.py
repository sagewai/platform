# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Graph intelligence — conversation-to-graph pipeline and memory consolidation.

Phase I8: ConversationGraphBuilder — incremental knowledge graph from chat turns.
Phase I9: MemoryConsolidator — dedup, decay, contradiction detection.
"""

from sagewai.intelligence.graph.builder import (
    ConversationGraphBuilder,
    GraphBuildResult,
)
from sagewai.intelligence.graph.consolidator import (
    ConsolidationResult,
    MemoryConsolidator,
)

__all__ = [
    "ConsolidationResult",
    "ConversationGraphBuilder",
    "GraphBuildResult",
    "MemoryConsolidator",
]
