# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""ProviderRegistry — tiered fallback chains for intelligence providers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sagewai.intelligence.config import IntelligenceConfig
from sagewai.intelligence.embeddings.hash_embedder import HashEmbedder
from sagewai.intelligence.embeddings.litellm_embedder import LiteLLMEmbedder
from sagewai.intelligence.embeddings.protocol import Embedder
from sagewai.intelligence.extractors.protocol import FactExtractor
from sagewai.intelligence.extractors.rule_based import RuleBasedFactExtractor
from sagewai.intelligence.multimodal.protocol import Transcriber, VisionDescriber
from sagewai.intelligence.summarizer.protocol import Summarizer

if TYPE_CHECKING:
    from sagewai.intelligence.extractors.protocol import (
        EntityExtractor,
        RelationExtractor,
    )
    from sagewai.intelligence.graph.builder import ConversationGraphBuilder
    from sagewai.intelligence.graph.consolidator import MemoryConsolidator

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Manages tiered fallback chains for intelligence providers.

    The ``get_embedder`` factory returns the best available backend based
    on config and installed packages::

        embedder = ProviderRegistry.get_embedder()  # auto-detect
        vectors = await embedder.embed(["hello world"])

    Fallback order for ``"auto"`` mode:

    1. **local** (sentence-transformers) — no API key, CPU-only
    2. **api** (LiteLLM) — requires API key
    3. **hash** — deterministic fallback, always works
    """

    @staticmethod
    def get_embedder(config: IntelligenceConfig | None = None) -> Embedder:
        """Get the best available embedder based on config and installed packages.

        Args:
            config: Intelligence configuration. Uses defaults when ``None``.

        Returns:
            An ``Embedder`` instance ready for use.
        """
        config = config or IntelligenceConfig()

        if config.embedding_provider == "local":
            return _try_local(config)

        if config.embedding_provider == "api":
            return LiteLLMEmbedder(
                model=config.embedding_api_model,
                dimension=config.embedding_api_dimension,
            )

        if config.embedding_provider == "hash":
            return HashEmbedder()

        # "auto" — try local, then API, then hash
        try:
            return _try_local(config)
        except ImportError:
            logger.info(
                "sentence-transformers not installed, falling back to API embedder"
            )

        try:
            return LiteLLMEmbedder(
                model=config.embedding_api_model,
                dimension=config.embedding_api_dimension,
            )
        except Exception:
            logger.info("LiteLLM embedder unavailable, falling back to hash embedder")

        return HashEmbedder()

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    @staticmethod
    def get_entity_extractor(
        config: IntelligenceConfig | None = None,
    ) -> EntityExtractor:
        """Get the best available entity extractor.

        Fallback order for ``"auto"`` mode:

        1. **local** — GLiNER (deterministic, CPU-only)
        2. **llm** — LiteLLM prompt

        Args:
            config: Intelligence configuration.  Uses defaults when ``None``.

        Returns:
            An ``EntityExtractor`` instance.
        """
        config = config or IntelligenceConfig()

        if config.extraction_provider == "local":
            return _try_gliner_entity(config)

        if config.extraction_provider == "llm":
            from sagewai.intelligence.extractors.llm_extractor import (
                LLMEntityExtractor,
            )

            return LLMEntityExtractor(model=config.extraction_llm_model)

        # "auto" — try GLiNER first
        try:
            return _try_gliner_entity(config)
        except ImportError:
            logger.info(
                "gliner not installed, falling back to LLM entity extractor"
            )

        from sagewai.intelligence.extractors.llm_extractor import (
            LLMEntityExtractor,
        )

        return LLMEntityExtractor(model=config.extraction_llm_model)

    @staticmethod
    def get_relation_extractor(
        config: IntelligenceConfig | None = None,
        entity_extractor: EntityExtractor | None = None,
    ) -> RelationExtractor:
        """Get the best available relation extractor.

        Fallback order for ``"auto"`` mode:

        1. **local** — :class:`HeuristicRelationExtractor` (requires an
           :class:`EntityExtractor`, auto-created if not provided)
        2. **llm** — :class:`LLMRelationExtractor`

        Args:
            config: Intelligence configuration.
            entity_extractor: Optional entity extractor for the heuristic
                backend.  Auto-created when ``None`` and local mode is used.

        Returns:
            A ``RelationExtractor`` instance.
        """
        config = config or IntelligenceConfig()

        if config.extraction_provider == "llm":
            from sagewai.intelligence.extractors.llm_extractor import (
                LLMRelationExtractor,
            )

            return LLMRelationExtractor(model=config.extraction_llm_model)

        if config.extraction_provider == "local":
            ner = entity_extractor or _try_gliner_entity(config)
            from sagewai.intelligence.extractors.gliner_extractor import (
                HeuristicRelationExtractor,
            )

            return HeuristicRelationExtractor(entity_extractor=ner)

        # "auto" — try heuristic (GLiNER-backed) first
        try:
            ner = entity_extractor or _try_gliner_entity(config)
            from sagewai.intelligence.extractors.gliner_extractor import (
                HeuristicRelationExtractor,
            )

            return HeuristicRelationExtractor(entity_extractor=ner)
        except ImportError:
            logger.info(
                "gliner not installed, falling back to LLM relation extractor"
            )

        from sagewai.intelligence.extractors.llm_extractor import (
            LLMRelationExtractor,
        )

        return LLMRelationExtractor(model=config.extraction_llm_model)

    # ------------------------------------------------------------------
    # Graph pipeline
    # ------------------------------------------------------------------

    @staticmethod
    def get_graph_builder(
        config: IntelligenceConfig | None = None,
        graph_store: object | None = None,
    ) -> ConversationGraphBuilder:
        """Create a graph builder with the best available extractors.

        Args:
            config: Intelligence configuration.  Uses defaults when ``None``.
            graph_store: Optional ``GraphMemory`` or ``NebulaGraphMemory``
                for persisting extracted entities and relations.

        Returns:
            A :class:`ConversationGraphBuilder` instance.
        """
        from sagewai.intelligence.graph.builder import ConversationGraphBuilder

        config = config or IntelligenceConfig()
        ner = ProviderRegistry.get_entity_extractor(config)
        rel = ProviderRegistry.get_relation_extractor(config, entity_extractor=ner)
        return ConversationGraphBuilder(
            entity_extractor=ner,
            relation_extractor=rel,
            graph_store=graph_store,
        )

    @staticmethod
    def get_consolidator(
        config: IntelligenceConfig | None = None,
        embedder: Embedder | None = None,
        similarity_threshold: float = 0.9,
        decay_rate: float = 0.01,
    ) -> MemoryConsolidator:
        """Create a memory consolidator with the best available embedder.

        When *embedder* is ``None`` and *config* is available, the registry
        auto-detects the best embedder via :meth:`get_embedder`.

        Args:
            config: Intelligence configuration.
            embedder: Explicit embedder override.
            similarity_threshold: Cosine similarity threshold for dedup.
            decay_rate: Exponential decay rate per day.

        Returns:
            A :class:`MemoryConsolidator` instance.
        """
        from sagewai.intelligence.graph.consolidator import MemoryConsolidator

        if embedder is None:
            embedder = ProviderRegistry.get_embedder(config)
        return MemoryConsolidator(
            embedder=embedder,
            similarity_threshold=similarity_threshold,
            decay_rate=decay_rate,
        )

    # ------------------------------------------------------------------
    # Multimodal
    # ------------------------------------------------------------------

    @staticmethod
    def get_transcriber(
        config: IntelligenceConfig | None = None,
    ) -> Transcriber | None:
        """Get the best available transcriber based on config.

        Fallback order for ``"auto"`` mode:

        1. **local** — faster-whisper (CPU, no API key)
        2. **api** — LiteLLM transcription API

        Returns ``None`` when provider is ``"disabled"`` or no backend
        is available.

        Args:
            config: Intelligence configuration. Uses defaults when ``None``.

        Returns:
            A ``Transcriber`` instance, or ``None`` if disabled/unavailable.
        """
        config = config or IntelligenceConfig()

        if config.transcription_provider == "disabled":
            return None

        if config.transcription_provider == "local":
            return _try_faster_whisper(config)

        if config.transcription_provider == "api":
            from sagewai.intelligence.multimodal.whisper import (
                LiteLLMTranscriber,
            )

            return LiteLLMTranscriber()

        # "auto" — try local first, then API
        try:
            return _try_faster_whisper(config)
        except ImportError:
            logger.info(
                "faster-whisper not installed, falling back to API transcriber"
            )

        try:
            import litellm  # noqa: F401

            from sagewai.intelligence.multimodal.whisper import (
                LiteLLMTranscriber,
            )

            return LiteLLMTranscriber()
        except ImportError:
            logger.info("litellm not available, transcription disabled")

        return None

    @staticmethod
    def get_vision_describer(
        config: IntelligenceConfig | None = None,
    ) -> VisionDescriber:
        """Get the best available vision describer based on config.

        Fallback order for ``"auto"`` mode:

        1. **api** — LLM vision via LiteLLM
        2. **stub** — placeholder (always works)

        Args:
            config: Intelligence configuration. Uses defaults when ``None``.

        Returns:
            A ``VisionDescriber`` instance.
        """
        config = config or IntelligenceConfig()

        if config.vision_provider == "disabled":
            from sagewai.intelligence.multimodal.vision import (
                StubVisionDescriber,
            )

            return StubVisionDescriber()

        if config.vision_provider == "api":
            from sagewai.intelligence.multimodal.vision import (
                LLMVisionDescriber,
            )

            return LLMVisionDescriber(model=config.vision_model)

        # "auto" — try LLM vision, fallback to stub
        try:
            import litellm  # noqa: F401

            from sagewai.intelligence.multimodal.vision import (
                LLMVisionDescriber,
            )

            return LLMVisionDescriber(model=config.vision_model)
        except ImportError:
            logger.info(
                "litellm not available, falling back to stub vision describer"
            )

        from sagewai.intelligence.multimodal.vision import StubVisionDescriber

        return StubVisionDescriber()

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------

    @staticmethod
    def get_summarizer(
        config: IntelligenceConfig | None = None,
        embedder: Embedder | None = None,
    ) -> Summarizer | None:
        """Get the best available summarizer based on config.

        Fallback order for ``"auto"`` mode:

        1. **semantic** — embedding-based sentence scoring
        2. ``None`` — caller falls back to keyword-overlap compression

        Args:
            config: Intelligence configuration. Uses defaults when ``None``.
            embedder: Optional pre-existing embedder to reuse.  Auto-detected
                when ``None``.

        Returns:
            A ``Summarizer`` instance, or ``None`` when keyword-overlap
            fallback should be used.
        """
        config = config or IntelligenceConfig()
        provider = config.summarizer_provider

        if provider == "keyword":
            return None

        if provider == "abstractive":
            from sagewai.intelligence.summarizer.abstractive import (
                BARTSummarizer,
            )

            return BARTSummarizer()

        if provider == "semantic":
            from sagewai.intelligence.summarizer.semantic import (
                SemanticSummarizer,
            )

            emb = embedder or ProviderRegistry.get_embedder(config)
            return SemanticSummarizer(embedder=emb)

        # "auto" — try semantic (always works since hash embedder is fallback)
        try:
            from sagewai.intelligence.summarizer.semantic import (
                SemanticSummarizer,
            )

            emb = embedder or ProviderRegistry.get_embedder(config)
            return SemanticSummarizer(embedder=emb)
        except Exception:  # noqa: BLE001
            logger.info(
                "Semantic summarizer unavailable, using keyword-overlap fallback"
            )

        return None


def _try_faster_whisper(config: IntelligenceConfig) -> Transcriber:
    """Attempt to create a faster-whisper transcriber."""
    from sagewai.intelligence.multimodal.whisper import FasterWhisperTranscriber

    return FasterWhisperTranscriber(model_size=config.transcription_model)


def _try_gliner_entity(config: IntelligenceConfig) -> EntityExtractor:
    """Attempt to create a GLiNER entity extractor."""
    from sagewai.intelligence.extractors.gliner_extractor import (
        GLiNEREntityExtractor,
    )

    return GLiNEREntityExtractor(
        model_name=config.extraction_model,
        threshold=config.extraction_confidence_threshold,
    )


    @staticmethod
    def get_fact_extractor(
        config: IntelligenceConfig | None = None,
    ) -> FactExtractor:
        """Get the best available fact extractor based on config.

        Fallback order for ``"auto"`` mode:

        1. **hybrid** — rules + LLM if litellm importable
        2. **rules** — pure pattern matching (always works)

        Args:
            config: Intelligence configuration. Uses defaults when ``None``.

        Returns:
            A ``FactExtractor`` instance ready for use.
        """
        config = config or IntelligenceConfig()
        provider = config.fact_extraction_provider

        if provider == "rules":
            return RuleBasedFactExtractor()

        if provider == "llm":
            from sagewai.intelligence.extractors.llm_fact_extractor import (
                LLMFactExtractor,
            )

            return LLMFactExtractor(model=config.fact_extraction_model)

        if provider == "hybrid":
            from sagewai.intelligence.extractors.hybrid_fact_extractor import (
                HybridFactExtractor,
            )
            from sagewai.intelligence.extractors.llm_fact_extractor import (
                LLMFactExtractor,
            )

            return HybridFactExtractor(
                rule_extractor=RuleBasedFactExtractor(),
                llm_extractor=LLMFactExtractor(
                    model=config.fact_extraction_model,
                ),
            )

        # "auto" — hybrid if litellm available, else rules-only
        try:
            import litellm  # noqa: F401

            from sagewai.intelligence.extractors.hybrid_fact_extractor import (
                HybridFactExtractor,
            )
            from sagewai.intelligence.extractors.llm_fact_extractor import (
                LLMFactExtractor,
            )

            logger.info(
                "Auto-detected litellm; using hybrid fact extractor"
            )
            return HybridFactExtractor(
                rule_extractor=RuleBasedFactExtractor(),
                llm_extractor=LLMFactExtractor(
                    model=config.fact_extraction_model,
                ),
            )
        except ImportError:
            logger.info(
                "litellm not installed; using rule-based fact extractor"
            )
            return RuleBasedFactExtractor()


def _try_local(config: IntelligenceConfig) -> Embedder:
    """Attempt to create a local sentence-transformer embedder."""
    from sagewai.intelligence.embeddings.sentence_transformer import (
        SentenceTransformerEmbedder,
    )

    return SentenceTransformerEmbedder(model_name=config.embedding_model)
