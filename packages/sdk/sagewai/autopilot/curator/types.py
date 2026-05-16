# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Frozen types for the autopilot curator subpackage.

``TrainingDataset``
    In-memory, project-scoped store of training samples for one dataset.
    Format-aware: accepted format values are ``"alpaca"``, ``"sharegpt"``,
    ``"classification"``, and ``"raw"``.

``FineTuneJob``
    Job specification emitted by the :class:`Curator` when a dataset
    crosses its sample threshold. The caller is responsible for actually
    scheduling the job (Unsloth, LoRA, etc.) — this type is purely
    declarative.

``PromotionResult``
    Frozen outcome from :class:`Promoter.promote`. ``promoted=True``
    means the candidate passed every metric gate in the blueprint's
    ``LearningLoopConfig.promotion_criteria``.

``CuratorConfig``
    Injectable configuration for :class:`Curator`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_KNOWN_FORMATS = ("alpaca", "sharegpt", "classification", "raw")

DatasetFormat = Literal["alpaca", "sharegpt", "classification", "raw"]


class TrainingDataset(BaseModel):
    """In-memory, project-scoped training dataset.

    Attributes:
        dataset_id: Unique name for this dataset (may be template-expanded,
            e.g. ``"triage_ds_proj-finance"``).
        project_id: Multi-tenant scope. Datasets from different projects
            must never be mixed.
        format: Wire format for downstream fine-tuning.  One of
            ``"alpaca"``, ``"sharegpt"``, ``"classification"``, ``"raw"``.
        samples: List of training samples. Each sample is a plain dict
            whose keys depend on ``format``.
    """

    model_config = ConfigDict(frozen=True)

    dataset_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    format: DatasetFormat
    samples: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def sample_count(self) -> int:
        """Number of samples currently in the dataset."""
        return len(self.samples)


class FineTuneJob(BaseModel):
    """Specification for a fine-tuning job.

    Emitted by :class:`Curator` when a dataset crosses the threshold
    defined in :attr:`LearningLoopConfig.trigger_after_labeled_samples`.
    The actual compute (Unsloth, LoRA, QLoRA, …) happens outside this
    layer.

    Attributes:
        job_id: Unique job identifier (UUID or sequential).
        dataset_id: Source dataset for fine-tuning.
        base_model: HuggingFace model ID or Ollama tag of the base model.
        project_id: Multi-tenant scope.
        method: Fine-tuning strategy — defaults to ``"unsloth"``.
        deploy_as: Deployment target after fine-tuning — defaults to
            ``"ollama"``.
        status: Lifecycle state.  ``"pending"`` → ``"running"`` →
            ``"completed"`` or ``"failed"``.
        error: Non-``None`` when ``status == "failed"``.
    """

    model_config = ConfigDict(frozen=True)

    job_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    base_model: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    method: str = "unsloth"
    deploy_as: str = "ollama"
    status: str = "pending"
    error: str | None = None


class PromotionResult(BaseModel):
    """Frozen outcome of a :class:`Promoter` evaluation.

    Attributes:
        promoted: ``True`` if the candidate passed every metric gate.
        reason: Human-readable explanation — includes the criteria string
            and observed metric values so the caller can surface it in
            logs or the admin UI.
        metrics: The metric values that were evaluated (same dict passed
            to :meth:`Promoter.promote`).
        candidate_model_id: The model that was evaluated.
    """

    model_config = ConfigDict(frozen=True)

    promoted: bool
    reason: str = Field(min_length=1)
    metrics: dict[str, float]
    candidate_model_id: str = Field(min_length=1)


class CuratorConfig(BaseModel):
    """Injectable configuration for :class:`Curator`.

    Attributes:
        max_queue_size: Maximum number of :class:`MissionRunResult` objects
            held in the internal deduplication buffer before older entries
            are evicted.
        deduplicate_by_mission_id: When ``True`` (default), a run result
            whose ``mission_id`` has already been processed is silently
            dropped, preventing duplicate training samples on retries.
    """

    model_config = ConfigDict(frozen=True)

    max_queue_size: int = Field(default=1000, gt=0)
    deduplicate_by_mission_id: bool = True
