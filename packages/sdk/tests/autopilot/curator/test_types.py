# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for TrainingDataset, FineTuneJob, PromotionResult, CuratorConfig."""

from __future__ import annotations

import pytest

from sagewai.autopilot.curator.types import (
    CuratorConfig,
    FineTuneJob,
    PromotionResult,
    TrainingDataset,
)

# ── TrainingDataset ────────────────────────────────────────────────


def test_training_dataset_stores_fields():
    ds = TrainingDataset(
        dataset_id="ds-001",
        project_id="proj-a",
        format="alpaca",
        samples=[{"instruction": "x", "output": "y"}],
    )
    assert ds.dataset_id == "ds-001"
    assert ds.project_id == "proj-a"
    assert ds.format == "alpaca"
    assert len(ds.samples) == 1


def test_training_dataset_empty_samples():
    ds = TrainingDataset(dataset_id="ds-002", project_id="proj-a", format="sharegpt")
    assert ds.samples == []


def test_training_dataset_sample_count():
    ds = TrainingDataset(
        dataset_id="ds-003",
        project_id="proj-b",
        format="classification",
        samples=[{"label": "a"}, {"label": "b"}, {"label": "c"}],
    )
    assert ds.sample_count == 3


def test_training_dataset_is_immutable():
    ds = TrainingDataset(dataset_id="ds-004", project_id="p", format="alpaca")
    with pytest.raises(Exception):
        ds.dataset_id = "changed"  # type: ignore[misc]


def test_training_dataset_requires_non_empty_dataset_id():
    with pytest.raises(Exception):
        TrainingDataset(dataset_id="", project_id="p", format="alpaca")


def test_training_dataset_requires_non_empty_project_id():
    with pytest.raises(Exception):
        TrainingDataset(dataset_id="ds-x", project_id="", format="alpaca")


def test_training_dataset_known_formats():
    for fmt in ("alpaca", "sharegpt", "classification", "raw"):
        ds = TrainingDataset(dataset_id="ds-x", project_id="p", format=fmt)
        assert ds.format == fmt


def test_training_dataset_rejects_unknown_format():
    with pytest.raises(Exception):
        TrainingDataset(dataset_id="ds-x", project_id="p", format="invalid_format")


def test_training_dataset_with_samples_returns_correct_count():
    samples = [{"i": str(n)} for n in range(10)]
    ds = TrainingDataset(dataset_id="ds-big", project_id="p", format="raw", samples=samples)
    assert ds.sample_count == 10


# ── FineTuneJob ────────────────────────────────────────────────────


def test_fine_tune_job_defaults():
    job = FineTuneJob(
        job_id="job-001",
        dataset_id="ds-001",
        base_model="llama-3.1-8b-instruct",
        project_id="proj-a",
    )
    assert job.status == "pending"
    assert job.method == "unsloth"
    assert job.deploy_as == "ollama"
    assert job.error is None


def test_fine_tune_job_custom_method():
    job = FineTuneJob(
        job_id="job-002",
        dataset_id="ds-002",
        base_model="mistral-7b",
        project_id="proj-b",
        method="lora",
        deploy_as="vllm",
        status="running",
    )
    assert job.method == "lora"
    assert job.deploy_as == "vllm"
    assert job.status == "running"


def test_fine_tune_job_is_immutable():
    job = FineTuneJob(
        job_id="job-003",
        dataset_id="ds-003",
        base_model="phi-3-mini",
        project_id="p",
    )
    with pytest.raises(Exception):
        job.status = "done"  # type: ignore[misc]


def test_fine_tune_job_requires_non_empty_ids():
    with pytest.raises(Exception):
        FineTuneJob(job_id="", dataset_id="ds-x", base_model="m", project_id="p")
    with pytest.raises(Exception):
        FineTuneJob(job_id="j", dataset_id="", base_model="m", project_id="p")
    with pytest.raises(Exception):
        FineTuneJob(job_id="j", dataset_id="ds-x", base_model="", project_id="p")
    with pytest.raises(Exception):
        FineTuneJob(job_id="j", dataset_id="ds-x", base_model="m", project_id="")


def test_fine_tune_job_with_error():
    job = FineTuneJob(
        job_id="job-004",
        dataset_id="ds-004",
        base_model="llama-3.1-8b",
        project_id="p",
        status="failed",
        error="GPU OOM",
    )
    assert job.error == "GPU OOM"
    assert job.status == "failed"


# ── PromotionResult ────────────────────────────────────────────────


def test_promotion_result_promoted():
    result = PromotionResult(
        promoted=True,
        reason="All criteria met: accuracy=0.94 cost=0.45",
        metrics={"accuracy": 0.94, "cost": 0.45},
        candidate_model_id="llama-3.1-8b-finetuned-v2",
    )
    assert result.promoted is True
    assert "accuracy" in result.reason
    assert result.metrics["accuracy"] == pytest.approx(0.94)
    assert result.candidate_model_id == "llama-3.1-8b-finetuned-v2"


def test_promotion_result_not_promoted():
    result = PromotionResult(
        promoted=False,
        reason="accuracy=0.88 < threshold 0.92",
        metrics={"accuracy": 0.88},
        candidate_model_id="llama-3.1-8b-finetuned-v1",
    )
    assert result.promoted is False
    assert "0.88" in result.reason


def test_promotion_result_is_immutable():
    result = PromotionResult(
        promoted=True,
        reason="ok",
        metrics={},
        candidate_model_id="model-x",
    )
    with pytest.raises(Exception):
        result.promoted = False  # type: ignore[misc]


def test_promotion_result_requires_non_empty_reason():
    with pytest.raises(Exception):
        PromotionResult(promoted=True, reason="", metrics={}, candidate_model_id="m")


def test_promotion_result_requires_non_empty_candidate_model_id():
    with pytest.raises(Exception):
        PromotionResult(promoted=False, reason="nope", metrics={}, candidate_model_id="")


def test_promotion_result_empty_metrics_allowed():
    result = PromotionResult(
        promoted=False,
        reason="no metrics collected",
        metrics={},
        candidate_model_id="model-y",
    )
    assert result.metrics == {}


# ── CuratorConfig ──────────────────────────────────────────────────


def test_curator_config_defaults():
    cfg = CuratorConfig()
    assert cfg.max_queue_size > 0
    assert cfg.deduplicate_by_mission_id is True


def test_curator_config_custom():
    cfg = CuratorConfig(max_queue_size=50, deduplicate_by_mission_id=False)
    assert cfg.max_queue_size == 50
    assert cfg.deduplicate_by_mission_id is False


def test_curator_config_is_immutable():
    cfg = CuratorConfig()
    with pytest.raises(Exception):
        cfg.max_queue_size = 999  # type: ignore[misc]


def test_curator_config_max_queue_size_must_be_positive():
    with pytest.raises(Exception):
        CuratorConfig(max_queue_size=0)
    with pytest.raises(Exception):
        CuratorConfig(max_queue_size=-1)
