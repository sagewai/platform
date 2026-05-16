# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Token budget allocator for directive resolution.

Allocates a fixed token budget across directive categories (context, tools,
few-shot, instructions) based on the model profile. Enforces limits so the
resolved content fits within the target model's context window.
"""

from __future__ import annotations

from sagewai.directives.ast import ContextBlock, DirectiveType
from sagewai.directives.profiles import ModelProfile


# Shared constant: average characters per token for estimation.
# Used by both directives.budget and core.compactor.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count from text using the ~4 chars/token heuristic.

    This matches the estimator used in ``sagewai.core.compactor``.
    """
    return max(1, len(text) // CHARS_PER_TOKEN)


class TokenBudget:
    """Manages token allocation across directive categories."""

    def __init__(self, profile: ModelProfile, override_total: int | None = None) -> None:
        self._total = override_total or profile.max_context_tokens
        self._allocations = {
            k: int(v * self._total)
            for k, v in profile.context_budget.items()
        }
        self._used: dict[str, int] = {k: 0 for k in self._allocations}

    @property
    def total(self) -> int:
        return self._total

    @property
    def remaining(self) -> int:
        return self._total - sum(self._used.values())

    def allocation(self, category: str) -> int:
        """Get the token budget for a category."""
        return self._allocations.get(category, 0)

    def used(self, category: str) -> int:
        """Get tokens used in a category."""
        return self._used.get(category, 0)

    def available(self, category: str) -> int:
        """Get remaining tokens in a category."""
        return max(0, self.allocation(category) - self.used(category))

    def consume(self, category: str, tokens: int) -> int:
        """Record token usage. Returns actual tokens consumed (may be capped)."""
        available = self.available(category)
        consumed = min(tokens, available)
        self._used[category] = self._used.get(category, 0) + consumed
        return consumed

    def can_fit(self, category: str, text: str) -> bool:
        """Check if text fits within the category budget."""
        return estimate_tokens(text) <= self.available(category)


def _category_for_directive(dtype: DirectiveType) -> str:
    """Map directive type to budget category."""
    if dtype in (DirectiveType.CONTEXT, DirectiveType.MEMORY):
        return "context"
    elif dtype in (DirectiveType.TOOL, DirectiveType.MCP):
        return "tools"
    elif dtype == DirectiveType.AGENT:
        return "context"  # Agent responses are contextual
    return "instructions"


def apply_budget(
    blocks: list[ContextBlock],
    budget: TokenBudget,
) -> list[ContextBlock]:
    """Filter and truncate context blocks to fit within the token budget.

    Blocks are processed in relevance order (highest first). Each block is
    either included in full, truncated to fit, or dropped entirely.
    """
    result: list[ContextBlock] = []

    # Process in relevance order (already sorted by resolver)
    for block in blocks:
        category = _category_for_directive(block.directive_type)
        tokens = estimate_tokens(block.content)
        available = budget.available(category)

        if available <= 0:
            continue

        if tokens <= available:
            # Fits entirely
            budget.consume(category, tokens)
            result.append(block)
        else:
            # Truncate content to fit the available budget exactly
            char_limit = available * 4  # reverse of token estimate
            truncated = block.content[:char_limit].rsplit(" ", 1)[0]
            if truncated:
                truncated_tokens = estimate_tokens(truncated)
                budget.consume(category, truncated_tokens)
                result.append(ContextBlock(
                    source=block.source,
                    content=truncated + "...",
                    relevance=block.relevance,
                    directive_type=block.directive_type,
                ))

    return result
