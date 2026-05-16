# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Chunking strategies for splitting documents into manageable pieces."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sagewai.context.models import ChunkingConfig, ChunkText

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

_tiktoken_enc = None


def _count_tokens(text: str) -> int:
    """Count tokens using tiktoken (cl100k_base) with fallback to char estimate."""
    global _tiktoken_enc
    try:
        if _tiktoken_enc is None:
            import tiktoken

            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        return len(_tiktoken_enc.encode(text))
    except ImportError:
        # Fallback: ~4 chars per token for English text
        return max(1, len(text) // 4)


def _content_hash(text: str) -> str:
    """SHA-256 hash of text content for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# ChunkManager
# ---------------------------------------------------------------------------


class ChunkManager:
    """Split text into chunks using configurable strategies.

    Supports:
    - ``fixed``: Split at exact token boundaries.
    - ``recursive``: Split using a hierarchy of separators, keeping chunks
      below the token budget while respecting natural boundaries.

    Usage::

        mgr = ChunkManager(ChunkingConfig(strategy="recursive", chunk_size=800))
        chunks = mgr.chunk("long text ...", metadata={"source": "readme.md"})
    """

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()

    def chunk(
        self, text: str, metadata: dict[str, Any] | None = None
    ) -> list[ChunkText]:
        """Split *text* into chunks according to the configured strategy."""
        if not text.strip():
            return []

        if self.config.strategy == "fixed":
            pieces = self._split_fixed(text)
        else:
            pieces = self._split_recursive(text, self.config.separators)

        base_meta = metadata or {}
        results: list[ChunkText] = []
        for idx, piece in enumerate(pieces):
            tc = _count_tokens(piece)
            results.append(
                ChunkText(
                    content=piece,
                    chunk_index=idx,
                    token_count=tc,
                    content_hash=_content_hash(piece),
                    metadata={**base_meta},
                )
            )
        return results

    # ------------------------------------------------------------------
    # Fixed strategy
    # ------------------------------------------------------------------

    def _split_fixed(self, text: str) -> list[str]:
        """Split text into fixed-size token chunks with overlap."""
        try:
            import tiktoken

            enc = tiktoken.get_encoding("cl100k_base")
            tokens = enc.encode(text)
        except ImportError:
            # Fallback: treat 4 chars as 1 token
            tokens = list(range(0, len(text), 4))
            return self._split_fixed_by_chars(text)

        step = max(1, self.config.chunk_size - self.config.chunk_overlap)
        pieces: list[str] = []
        for start in range(0, len(tokens), step):
            end = min(start + self.config.chunk_size, len(tokens))
            piece = enc.decode(tokens[start:end])
            if piece.strip():
                pieces.append(piece.strip())
            if end >= len(tokens):
                break
        return pieces

    def _split_fixed_by_chars(self, text: str) -> list[str]:
        """Char-based fixed splitting (tiktoken unavailable)."""
        char_size = self.config.chunk_size * 4
        char_overlap = self.config.chunk_overlap * 4
        step = max(1, char_size - char_overlap)
        pieces: list[str] = []
        for start in range(0, len(text), step):
            end = min(start + char_size, len(text))
            piece = text[start:end].strip()
            if piece:
                pieces.append(piece)
            if end >= len(text):
                break
        return pieces

    # ------------------------------------------------------------------
    # Recursive strategy
    # ------------------------------------------------------------------

    def _split_recursive(self, text: str, separators: list[str]) -> list[str]:
        """Recursively split text using separator hierarchy."""
        if _count_tokens(text) <= self.config.chunk_size:
            return [text.strip()] if text.strip() else []

        if not separators:
            return self._split_fixed(text)

        sep = separators[0]
        remaining_seps = separators[1:]

        parts = text.split(sep)
        chunks: list[str] = []
        current = ""

        for part in parts:
            candidate = (current + sep + part) if current else part
            if _count_tokens(candidate) <= self.config.chunk_size:
                current = candidate
            else:
                if current.strip():
                    if _count_tokens(current) <= self.config.chunk_size:
                        chunks.append(current.strip())
                    else:
                        chunks.extend(self._split_recursive(current, remaining_seps))
                current = part

        if current.strip():
            if _count_tokens(current) <= self.config.chunk_size:
                chunks.append(current.strip())
            else:
                chunks.extend(self._split_recursive(current, remaining_seps))

        # Apply overlap by prepending tail of previous chunk
        if self.config.chunk_overlap > 0 and len(chunks) > 1:
            chunks = self._apply_overlap(chunks)

        return chunks

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        """Add overlap by prepending the tail of the previous chunk."""
        if len(chunks) <= 1:
            return chunks

        result = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            # Take last N tokens worth of chars from prev
            overlap_chars = self.config.chunk_overlap * 4  # approximate
            tail = prev[-overlap_chars:] if len(prev) > overlap_chars else prev
            # Find a clean break point
            for sep in ["\n", ". ", " "]:
                idx = tail.find(sep)
                if idx >= 0:
                    tail = tail[idx + len(sep) :]
                    break
            combined = tail + " " + chunks[i] if tail.strip() else chunks[i]
            result.append(combined.strip())
        return result
