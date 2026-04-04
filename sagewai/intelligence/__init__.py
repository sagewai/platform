# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Intelligence layer — pluggable AI backends for embeddings, extraction, and more.

Phase I1: Embedder protocol with local, API, and hash backends.
Phase I2: Language detection and universal sentence segmentation.
Phase I3: GLiNER-based NER and relation extraction.
Phase I4: Rule-based fact extraction + hybrid LLM enhancement.
Phase I6: Multimodal processing — transcription and vision protocols.
Phase I7: Semantic summarization replacing keyword-overlap compression.
Phase I8: Conversation-to-graph pipeline.
Phase I9: Memory consolidation — dedup, decay, contradiction detection.
"""

from sagewai.intelligence.config import IntelligenceConfig
from sagewai.intelligence.embeddings.hash_embedder import HashEmbedder
from sagewai.intelligence.embeddings.litellm_embedder import LiteLLMEmbedder
from sagewai.intelligence.embeddings.protocol import Embedder
from sagewai.intelligence.embeddings.sentence_transformer import (
    SentenceTransformerEmbedder,
)
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
from sagewai.intelligence.language.detector import LanguageDetector
from sagewai.intelligence.language.segmenter import UniversalSegmenter
from sagewai.intelligence.multimodal.message import ContentPart, ContentType
from sagewai.intelligence.models import (
    ExtractedFact,
    ExtractionResult,
    RelationTriple,
)
from sagewai.intelligence.multimodal.protocol import Transcriber, VisionDescriber
from sagewai.intelligence.multimodal.vision import (
    LLMVisionDescriber,
    StubVisionDescriber,
)
from sagewai.intelligence.multimodal.whisper import (
    FasterWhisperTranscriber,
    LiteLLMTranscriber,
)
from sagewai.intelligence.graph.builder import (
    ConversationGraphBuilder,
    GraphBuildResult,
)
from sagewai.intelligence.graph.consolidator import (
    ConsolidationResult,
    MemoryConsolidator,
)
from sagewai.intelligence.registry import ProviderRegistry
from sagewai.intelligence.summarizer.protocol import Summarizer
from sagewai.intelligence.summarizer.semantic import (
    SemanticSummarizer,
    cosine_similarity,
)

__all__ = [
    # Embeddings
    "Embedder",
    "HashEmbedder",
    "LiteLLMEmbedder",
    "SentenceTransformerEmbedder",
    # Extraction — models
    "ExtractedFact",
    "ExtractionResult",
    "RelationTriple",
    # Extraction — protocols
    "EntityExtractor",
    "FactExtractor",
    "RelationExtractor",
    # Extraction — backends
    "GLiNEREntityExtractor",
    "HeuristicRelationExtractor",
    "HybridFactExtractor",
    "LLMEntityExtractor",
    "LLMFactExtractor",
    "LLMRelationExtractor",
    "RuleBasedFactExtractor",
    # Language
    "LanguageDetector",
    "UniversalSegmenter",
    # Multimodal
    "ContentPart",
    "ContentType",
    "FasterWhisperTranscriber",
    "LiteLLMTranscriber",
    "LLMVisionDescriber",
    "StubVisionDescriber",
    "Transcriber",
    "VisionDescriber",
    # Summarization
    "SemanticSummarizer",
    "Summarizer",
    "cosine_similarity",
    # Graph pipeline
    "ConsolidationResult",
    "ConversationGraphBuilder",
    "GraphBuildResult",
    "MemoryConsolidator",
    # Config & registry
    "IntelligenceConfig",
    "ProviderRegistry",
]
