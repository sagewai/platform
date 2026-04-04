# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Model profiles for the Directive Engine.

Defines how resolved content is formatted for different model capability tiers.
Includes auto-detection from model name strings.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


class ModelProfile(BaseModel):
    """Defines how the Directive Engine formats output for a model class.

    The profile controls compression aggressiveness, delimiter style, tool-call
    mode, and token budget allocation — all tuned for the target model's
    capabilities and context window size.
    """

    name: str
    """Profile identifier: ``'small'``, ``'medium'``, or ``'large'``."""

    max_context_tokens: int = 4096
    """Effective context budget for directive results (not the full model window)."""

    compression_ratio: float = 1.0
    """Target compression ratio. 1.0 = no compression, 5.0 = aggressive."""

    max_few_shot: int = 3
    """Maximum number of few-shot examples to inject."""

    use_delimiters: bool = False
    """Wrap resolved content in structured ``[CONTEXT]`` / ``[SOURCE]`` delimiters."""

    use_explicit_instructions: bool = False
    """Add explicit ``You MUST use the context above`` framing for small models."""

    tool_call_mode: str = "native"
    """``'native'`` for models with function-calling support,
    ``'prompt_based'`` for models that need tools described in the prompt."""

    context_budget: dict[str, float] = Field(default_factory=lambda: {
        "context": 0.40,
        "tools": 0.25,
        "few_shot": 0.20,
        "instructions": 0.15,
    })
    """Token budget allocation per directive category (must sum to 1.0)."""

    default_top_k: int = 5
    """Default ``top_k`` for context retrieval when not specified in the directive."""

    min_block_tokens: int = 50
    """Minimum token budget per context block during compression."""

    sentence_boost_first: float = 1.3
    """Score multiplier for the first sentence during extractive compression."""

    sentence_boost_last: float = 1.1
    """Score multiplier for the last sentence during extractive compression."""

    tool_call_marker: str = "TOOL_CALL:"
    """Marker string small models output to invoke tools (prompt-based mode)."""


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

SMALL = ModelProfile(
    name="small",
    max_context_tokens=2048,
    compression_ratio=5.0,
    max_few_shot=1,
    use_delimiters=True,
    use_explicit_instructions=True,
    tool_call_mode="prompt_based",
    context_budget={
        "context": 0.35,
        "tools": 0.20,
        "few_shot": 0.15,
        "instructions": 0.30,
    },
)

MEDIUM = ModelProfile(
    name="medium",
    max_context_tokens=8192,
    compression_ratio=2.0,
    max_few_shot=3,
    use_delimiters=True,
    use_explicit_instructions=False,
    tool_call_mode="native",
    context_budget={
        "context": 0.40,
        "tools": 0.25,
        "few_shot": 0.20,
        "instructions": 0.15,
    },
)

LARGE = ModelProfile(
    name="large",
    max_context_tokens=32768,
    compression_ratio=1.0,
    max_few_shot=5,
    use_delimiters=False,
    use_explicit_instructions=False,
    tool_call_mode="native",
    context_budget={
        "context": 0.45,
        "tools": 0.30,
        "few_shot": 0.15,
        "instructions": 0.10,
    },
)

_PROFILES = {"small": SMALL, "medium": MEDIUM, "large": LARGE}


# ---------------------------------------------------------------------------
# Auto-detection patterns
# ---------------------------------------------------------------------------

# (regex_pattern, profile_name) — checked in order, first match wins
_MODEL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Small models (< 13B)
    (re.compile(r"codellama[:/]?(7b|13b)", re.I), "small"),
    (re.compile(r"llama[- ]?3\.?[12]?[:/]?(1b|3b|7b|8b)", re.I), "small"),
    (re.compile(r"mistral[:/]?(7b|nemo)", re.I), "small"),
    (re.compile(r"phi[- ]?[34][:/]?(mini|small|3b|7b|14b)?", re.I), "small"),
    (re.compile(r"gemma[:/]?(2b|7b|9b)", re.I), "small"),
    (re.compile(r"qwen2?\.?5?[:/]?(0\.5b|1\.5b|3b|7b)", re.I), "small"),
    (re.compile(r"tinyllama", re.I), "small"),
    (re.compile(r"stable(lm|code)[:/]?(3b|7b)", re.I), "small"),
    (re.compile(r"deepseek[- ]?coder[:/]?(1\.3b|6\.7b|7b)", re.I), "small"),
    (re.compile(r"yi[:/]?(6b|9b)", re.I), "small"),
    # Ollama shorthand without size — assume small
    (re.compile(r"^ollama/(codellama|mistral|phi|gemma|tinyllama|stablecode)$", re.I), "small"),

    # Medium models (13B-70B, or API models with smaller context)
    (re.compile(r"llama[- ]?3\.?[12]?[:/]?(13b|34b|70b)", re.I), "medium"),
    (re.compile(r"mixtral", re.I), "medium"),
    (re.compile(r"codellama[:/]?(34b|70b)", re.I), "medium"),
    (re.compile(r"qwen2?\.?5?[:/]?(14b|32b|72b)", re.I), "medium"),
    (re.compile(r"deepseek[- ]?coder[:/]?(33b)", re.I), "medium"),
    (re.compile(r"yi[:/]?(34b)", re.I), "medium"),
    (re.compile(r"gemma[:/]?(27b)", re.I), "medium"),
    (re.compile(r"gemini.*(flash|lite)", re.I), "medium"),
    (re.compile(r"claude.*haiku", re.I), "medium"),
    (re.compile(r"gpt-4o-mini", re.I), "medium"),
    (re.compile(r"gpt-4\.1-mini", re.I), "medium"),
    (re.compile(r"gpt-4\.1-nano", re.I), "small"),
    (re.compile(r"gpt-3\.5", re.I), "medium"),

    # Large models (frontier API models)
    (re.compile(r"gpt-4o(?!-mini)", re.I), "large"),
    (re.compile(r"gpt-4-turbo", re.I), "large"),
    (re.compile(r"gpt-4\.1(?!-(mini|nano))", re.I), "large"),
    # Claude: matches claude-sonnet-4-6, claude-opus-4-6, anthropic/claude-*
    (re.compile(r"claude[- ]?(sonnet|opus)", re.I), "large"),
    (re.compile(r"gemini.*(pro|ultra|advanced)", re.I), "large"),
    (re.compile(r"o[13](-mini|-preview)?$", re.I), "large"),
    (re.compile(r"deepseek[- ]?(v3|r1|reasoner)", re.I), "large"),
]


def detect_profile(model: str) -> ModelProfile:
    """Auto-detect the model profile from a model name string.

    Matches against known patterns. Falls back to ``MEDIUM`` for unknown models.

    Examples::

        detect_profile("codellama:7b-instruct")  → SMALL
        detect_profile("gpt-4o")                  → LARGE
        detect_profile("ollama/mistral")           → SMALL
        detect_profile("claude-sonnet-4-6")        → LARGE
        detect_profile("unknown-model-xyz")        → MEDIUM
    """
    for pattern, profile_name in _MODEL_PATTERNS:
        if pattern.search(model):
            return _PROFILES[profile_name].model_copy()
    return MEDIUM.model_copy()


def get_profile(name: str) -> ModelProfile:
    """Get a built-in profile by name."""
    if name not in _PROFILES:
        raise ValueError(f"Unknown profile: {name!r}. Choose from: {list(_PROFILES)}")
    return _PROFILES[name].model_copy()
