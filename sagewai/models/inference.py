# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Inference parameter presets and configuration."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class InferencePreset(str, Enum):
    """Predefined inference parameter bundles for common use cases."""

    PRECISE = "precise"
    BALANCED = "balanced"
    CREATIVE = "creative"
    EXPERIMENTAL = "experimental"


_PRESET_PARAMS: dict[InferencePreset, dict[str, Any]] = {
    InferencePreset.PRECISE: {"temperature": 0.1, "top_p": 0.9},
    InferencePreset.BALANCED: {"temperature": 0.5, "top_p": 0.95},
    InferencePreset.CREATIVE: {"temperature": 0.9, "top_p": 1.0},
    InferencePreset.EXPERIMENTAL: {"temperature": 1.5, "top_p": 1.0},
}


class InferenceParams(BaseModel):
    """LLM inference parameters with validation.

    Use directly for full control, or use ``from_preset()`` for convenience::

        # Preset
        params = InferenceParams.from_preset(InferencePreset.CREATIVE)

        # Preset with overrides
        params = InferenceParams.from_preset(InferencePreset.CREATIVE, max_tokens=2000)

        # Fully custom
        params = InferenceParams(temperature=0.3, top_p=0.8, max_tokens=4096)
    """

    temperature: float = 0.7
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    stop_sequences: list[str] | None = None
    # Custom endpoint / provider support
    api_base: str | None = None
    api_key: str | None = None
    custom_llm_provider: str | None = None
    # Timeout for a single LLM call in seconds (None = no timeout)
    timeout: float | None = 120.0
    # Ordered fallback models — tried in sequence on timeout/rate-limit/API error
    fallback_models: list[str] = Field(default_factory=list)

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError(f"temperature must be between 0.0 and 2.0, got {v}")
        return v

    @field_validator("top_p")
    @classmethod
    def validate_top_p(cls, v: float | None) -> float | None:
        if v is not None and not 0.0 <= v <= 1.0:
            raise ValueError(f"top_p must be between 0.0 and 1.0, got {v}")
        return v

    @field_validator("frequency_penalty")
    @classmethod
    def validate_frequency_penalty(cls, v: float | None) -> float | None:
        if v is not None and not -2.0 <= v <= 2.0:
            raise ValueError(f"frequency_penalty must be between -2.0 and 2.0, got {v}")
        return v

    @field_validator("presence_penalty")
    @classmethod
    def validate_presence_penalty(cls, v: float | None) -> float | None:
        if v is not None and not -2.0 <= v <= 2.0:
            raise ValueError(f"presence_penalty must be between -2.0 and 2.0, got {v}")
        return v

    @classmethod
    def from_preset(cls, preset: InferencePreset, **overrides: Any) -> InferenceParams:
        """Create inference params from a preset with optional overrides.

        Args:
            preset: The preset to use as a base.
            **overrides: Any parameter to override from the preset defaults.

        Returns:
            An InferenceParams instance with preset values and any overrides applied.
        """
        params = {**_PRESET_PARAMS[preset], **overrides}
        return cls(**params)
