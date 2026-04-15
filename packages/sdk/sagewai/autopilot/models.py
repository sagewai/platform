# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Auxiliary pydantic models composed into :class:`Blueprint`.

These are deliberately small: the "interesting" logic lives in
:mod:`sagewai.autopilot.blueprint`, :mod:`sagewai.autopilot.agent_graph`,
:mod:`sagewai.autopilot.slots`, and :mod:`sagewai.autopilot.mission`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ._types import Operator


class ProviderRequirement(BaseModel):
    """A declaration that a blueprint needs an LLM provider of some kind."""

    model_config = ConfigDict(frozen=True)

    role: str  # e.g. "summarizer", "classifier"
    capability: str  # e.g. "reasoning", "classification"
    tier: str  # e.g. "small", "medium", "large"
    fine_tune_target: bool = False  # Layer 5 learning-loop hook


class Metric(BaseModel):
    """A single numeric success-criterion check."""

    model_config = ConfigDict(frozen=True)

    name: str
    op: Operator
    value: float


class EvalRef(BaseModel):
    """Points a blueprint at a managed eval dataset + metric gates."""

    model_config = ConfigDict(frozen=True)

    dataset_id: str
    metrics: tuple[Metric, ...]


class TrainingHook(BaseModel):
    """Captures runs into a training dataset under a quality filter."""

    model_config = ConfigDict(frozen=True)

    event: str  # e.g. "summarizer.completed"
    dataset: str  # may contain {project_id}, {document_type}
    format: str  # "alpaca", "sharegpt", "classification"
    quality_filter: str | None = None  # e.g. "user_rating >= 4"


class LearningLoopConfig(BaseModel):
    """Layer 5 configuration for automatic fine-tune + promote."""

    model_config = ConfigDict(frozen=True)

    trigger_after_labeled_samples: int = Field(gt=0)
    base_model: str
    eval_gate_dataset_id: str
    promotion_criteria: str  # e.g. "accuracy >= 0.92 AND cost <= ..."
    fine_tune_method: str = "unsloth"
    deploy_as: str = "ollama"
