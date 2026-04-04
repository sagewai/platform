# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Intelligence layer configuration."""

from __future__ import annotations

from pydantic import BaseModel


class IntelligenceConfig(BaseModel):
    """Configuration for the intelligence layer.

    Controls which embedding backend is used and how fallback works.

    Attributes:
        embedding_provider: Backend selection strategy.
            ``"auto"`` tries local -> API -> hash in order.
            ``"local"`` uses sentence-transformers only.
            ``"api"`` uses LiteLLM only.
            ``"hash"`` uses deterministic hash fallback.
        embedding_model: Model name for the local sentence-transformers backend.
        embedding_api_model: Model name for the LiteLLM API backend.
        embedding_api_dimension: Vector dimension for the API model.
    """

    embedding_provider: str = "auto"
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_api_model: str = "text-embedding-3-small"
    embedding_api_dimension: int = 1536

    # -- Extraction ----------------------------------------------------------
    extraction_provider: str = "auto"
    """Backend selection for entity/relation extraction.

    ``"auto"`` tries GLiNER (local) first, falling back to LLM.
    ``"local"`` uses GLiNER only (raises if not installed).
    ``"llm"`` uses LiteLLM only.
    """
    extraction_confidence_threshold: float = 0.5
    """Minimum confidence for extracted entities to be retained."""
    extraction_entity_types: list[str] = []
    """Entity type labels. Empty list means the backend's defaults are used."""
    extraction_model: str = "urchade/gliner_medium-v2.1"
    """GLiNER model name for local extraction."""
    extraction_llm_model: str = "gpt-4o-mini"
    """LLM model name for LLM-based extraction fallback."""

    # -- Fact Extraction -----------------------------------------------------
    fact_extraction_provider: str = "auto"
    """Fact extraction backend selection.

    ``"auto"`` uses rules; adds LLM when litellm is importable.
    ``"rules"`` uses rule-based extraction only (no API key needed).
    ``"llm"`` uses LLM extraction only.
    ``"hybrid"`` uses rules first, LLM fills gaps.
    """
    fact_extraction_model: str = "gpt-4o-mini"
    """LLM model used by the ``llm`` and ``hybrid`` extractors."""

    # -- Multimodal -----------------------------------------------------------
    transcription_provider: str = "auto"
    """Transcription backend selection.

    ``"auto"`` tries local (faster-whisper) first, falls back to API.
    ``"local"`` uses faster-whisper only (raises if not installed).
    ``"api"`` uses LiteLLM transcription API only.
    ``"disabled"`` disables transcription entirely.
    """
    transcription_model: str = "base"
    """Model size for faster-whisper (``"tiny"``, ``"base"``, ``"small"``,
    ``"medium"``, ``"large-v3"``)."""

    vision_provider: str = "auto"
    """Vision description backend selection.

    ``"auto"`` uses LLM vision if litellm is available, else stub.
    ``"api"`` uses LLM vision only.
    ``"disabled"`` disables vision description entirely.
    """
    vision_model: str = "gpt-4o-mini"
    """LLM model for vision descriptions (must support image input)."""

    # -- Summarization --------------------------------------------------------
    summarizer_provider: str = "auto"
    """Summarization backend selection.

    ``"auto"`` uses semantic (embedding-based) if an embedder is available,
    otherwise falls back to keyword-overlap scoring (no Summarizer object).
    ``"semantic"`` uses embedding-based sentence scoring.
    ``"abstractive"`` uses BART (requires ``transformers`` + ``torch``).
    ``"keyword"`` disables the Summarizer and uses legacy keyword overlap.
    """
