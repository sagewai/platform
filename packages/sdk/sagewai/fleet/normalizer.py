# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Model name normalization for fleet routing.

Normalizes LLM model names to a canonical form so that workers
and runs can be matched regardless of how the model was specified
(e.g. ``openai/gpt-4o`` vs ``gpt-4o``).

Usage::

    from sagewai.fleet.normalizer import ModelNormalizer

    assert ModelNormalizer.normalize("openai/gpt-4o") == "gpt-4o"
    assert ModelNormalizer.normalize("ollama/llama3:70b") == "llama3-70b"
"""

from __future__ import annotations

import re


class ModelNormalizer:
    """Normalize model names to canonical form for matching.

    Rules applied in order:

    1. Strip leading/trailing whitespace.
    2. Lowercase the entire string.
    3. Remove provider prefix (everything before the first ``/``).
    4. Replace colons with hyphens (Ollama convention: ``llama3:70b``).
    5. Collapse multiple hyphens/underscores into single hyphens.
    6. Strip leading/trailing hyphens.
    """

    # Matches repeated hyphens, underscores, or mixed sequences
    _SEP_RE = re.compile(r"[-_]+")

    @staticmethod
    def normalize(model: str) -> str:
        """Normalize a single model name to canonical form.

        Args:
            model: Raw model name, possibly with provider prefix.

        Returns:
            Canonical lowercase model name suitable for matching.

        Examples:
            >>> ModelNormalizer.normalize("openai/gpt-4o")
            'gpt-4o'
            >>> ModelNormalizer.normalize("anthropic/claude-sonnet-4-6")
            'claude-sonnet-4-6'
            >>> ModelNormalizer.normalize("ollama/llama3:70b")
            'llama3-70b'
            >>> ModelNormalizer.normalize("  GPT-4o  ")
            'gpt-4o'
            >>> ModelNormalizer.normalize("")
            ''
        """
        if not model:
            return ""

        result = model.strip().lower()

        # Strip provider prefix (e.g. "openai/", "anthropic/", "ollama/")
        if "/" in result:
            result = result.split("/", 1)[1]

        # Replace remaining slashes and colons with hyphens
        result = result.replace("/", "-")
        result = result.replace(":", "-")

        # Collapse separator sequences into single hyphens
        result = ModelNormalizer._SEP_RE.sub("-", result)

        # Strip leading/trailing hyphens
        result = result.strip("-")

        return result

    @staticmethod
    def canonical_list(models: list[str]) -> list[str]:
        """Normalize a list of model names, deduplicate, preserve order.

        Args:
            models: List of raw model names.

        Returns:
            Deduplicated list of canonical model names.

        Examples:
            >>> ModelNormalizer.canonical_list(["openai/gpt-4o", "gpt-4o", "ollama/llama3:70b"])
            ['gpt-4o', 'llama3-70b']
        """
        seen: set[str] = set()
        result: list[str] = []
        for m in models:
            canonical = ModelNormalizer.normalize(m)
            if canonical and canonical not in seen:
                seen.add(canonical)
                result.append(canonical)
        return result
