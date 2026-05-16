# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for auxiliary autopilot pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.autopilot._types import Operator
from sagewai.autopilot.models import (
    EvalRef,
    LearningLoopConfig,
    Metric,
    ProviderRequirement,
    TrainingHook,
)


def test_provider_requirement_minimum_fields():
    pr = ProviderRequirement(role="summarizer", capability="reasoning", tier="medium")
    assert pr.role == "summarizer"
    assert pr.fine_tune_target is False  # default


def test_provider_requirement_fine_tune_target_true():
    pr = ProviderRequirement(
        role="classifier",
        capability="classification",
        tier="small",
        fine_tune_target=True,
    )
    assert pr.fine_tune_target is True


def test_metric_accepts_all_operators():
    for op in Operator:
        m = Metric(name="x", op=op.value, value=1.0)
        assert m.op is op


def test_metric_rejects_unknown_operator():
    with pytest.raises(ValidationError):
        Metric(name="x", op="~~", value=1.0)


def test_eval_ref_round_trips_through_json():
    ref = EvalRef(
        dataset_id="competitive-research-eval-v1",
        metrics=[
            Metric(name="item_coverage", op=">=", value=0.80),
            Metric(name="cost_per_run_usd", op="<=", value=0.15),
        ],
    )
    dumped = ref.model_dump_json()
    restored = EvalRef.model_validate_json(dumped)
    assert restored == ref


def test_training_hook_minimum_fields():
    hook = TrainingHook(
        event="summarizer.completed",
        dataset="competitive-research-summarizer",
        format="alpaca",
    )
    assert hook.quality_filter is None  # optional


def test_training_hook_with_quality_filter():
    hook = TrainingHook(
        event="router.completed",
        dataset="support-triage-classifier-{project_id}",
        format="classification",
        quality_filter="human_override is None",
    )
    assert hook.quality_filter == "human_override is None"


def test_learning_loop_config_minimum_fields():
    cfg = LearningLoopConfig(
        trigger_after_labeled_samples=500,
        base_model="llama-3.1-8b-instruct",
        eval_gate_dataset_id="support-triage-eval-v1",
        promotion_criteria="accuracy >= 0.92",
    )
    assert cfg.fine_tune_method == "unsloth"  # default
    assert cfg.deploy_as == "ollama"  # default


def test_learning_loop_config_rejects_nonpositive_threshold():
    with pytest.raises(ValidationError):
        LearningLoopConfig(
            trigger_after_labeled_samples=0,
            base_model="x",
            eval_gate_dataset_id="x",
            promotion_criteria="x",
        )
