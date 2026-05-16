# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the Promoter A/B eval gate."""

from __future__ import annotations

import pytest

from sagewai.autopilot.curator.promoter import Promoter
from sagewai.autopilot.curator.types import PromotionResult

# ── Basic promotion pass / fail ────────────────────────────────────


def test_promotes_when_all_criteria_pass():
    p = Promoter()
    result = p.promote(
        candidate_model_id="llama-3.1-8b-ft-v2",
        metrics={"accuracy": 0.94, "cost": 0.40},
        criteria="accuracy >= 0.92 AND cost <= 0.50",
    )
    assert isinstance(result, PromotionResult)
    assert result.promoted is True
    assert result.candidate_model_id == "llama-3.1-8b-ft-v2"


def test_does_not_promote_when_accuracy_below_threshold():
    p = Promoter()
    result = p.promote(
        candidate_model_id="model-v1",
        metrics={"accuracy": 0.88, "cost": 0.30},
        criteria="accuracy >= 0.92 AND cost <= 0.50",
    )
    assert result.promoted is False
    assert "0.88" in result.reason or "accuracy" in result.reason


def test_does_not_promote_when_cost_above_threshold():
    p = Promoter()
    result = p.promote(
        candidate_model_id="model-v1",
        metrics={"accuracy": 0.95, "cost": 0.60},
        criteria="accuracy >= 0.92 AND cost <= 0.50",
    )
    assert result.promoted is False


def test_promotes_with_or_criteria():
    p = Promoter()
    # accuracy below threshold but f1 meets OR branch
    result = p.promote(
        candidate_model_id="model-or",
        metrics={"accuracy": 0.85, "f1": 0.93},
        criteria="accuracy >= 0.92 OR f1 >= 0.90",
    )
    assert result.promoted is True


# ── Single-metric criteria ─────────────────────────────────────────


def test_single_metric_pass():
    p = Promoter()
    result = p.promote(
        candidate_model_id="m",
        metrics={"accuracy": 0.92},
        criteria="accuracy >= 0.92",
    )
    assert result.promoted is True


def test_single_metric_boundary_fail():
    p = Promoter()
    result = p.promote(
        candidate_model_id="m",
        metrics={"accuracy": 0.9199},
        criteria="accuracy >= 0.92",
    )
    assert result.promoted is False


# ── Missing metrics ────────────────────────────────────────────────


def test_missing_metric_does_not_promote():
    p = Promoter()
    result = p.promote(
        candidate_model_id="m",
        metrics={"cost": 0.30},
        criteria="accuracy >= 0.92",
    )
    assert result.promoted is False


def test_missing_metric_reason_is_descriptive():
    p = Promoter()
    result = p.promote(
        candidate_model_id="m",
        metrics={},
        criteria="accuracy >= 0.92",
    )
    assert result.promoted is False
    assert len(result.reason) > 0


# ── PromotionResult fields ─────────────────────────────────────────


def test_result_includes_candidate_model_id():
    p = Promoter()
    result = p.promote(
        candidate_model_id="special-model-xyz",
        metrics={"accuracy": 0.95},
        criteria="accuracy >= 0.90",
    )
    assert result.candidate_model_id == "special-model-xyz"


def test_result_includes_metrics_snapshot():
    p = Promoter()
    metrics = {"accuracy": 0.95, "latency": 120.0}
    result = p.promote(
        candidate_model_id="m",
        metrics=metrics,
        criteria="accuracy >= 0.90",
    )
    assert result.metrics == metrics


def test_promoted_result_reason_mentions_criteria():
    p = Promoter()
    result = p.promote(
        candidate_model_id="m",
        metrics={"accuracy": 0.95},
        criteria="accuracy >= 0.92",
    )
    assert "accuracy" in result.reason


# ── from_blueprint factory ─────────────────────────────────────────


def test_from_blueprint_extracts_criteria(event_driven_bp):
    p = Promoter.from_blueprint(event_driven_bp)
    # event_driven_bp.learning_loop_target.promotion_criteria == "accuracy >= 0.92"
    result = p.promote(
        candidate_model_id="model-from-bp",
        metrics={"accuracy": 0.93},
        criteria=event_driven_bp.learning_loop_target.promotion_criteria,
    )
    assert result.promoted is True


def test_from_blueprint_raises_if_no_learning_loop(scheduled_bp):
    """scheduled_bp has no learning_loop_target."""
    with pytest.raises(ValueError, match="learning_loop_target"):
        Promoter.from_blueprint(scheduled_bp)


# ── Promoter is stateless ──────────────────────────────────────────


def test_promoter_stateless_multiple_calls():
    p = Promoter()
    r1 = p.promote("m1", {"accuracy": 0.93}, "accuracy >= 0.92")
    r2 = p.promote("m2", {"accuracy": 0.80}, "accuracy >= 0.92")
    assert r1.promoted is True
    assert r2.promoted is False


# ── Complex criteria from real blueprint shapes ────────────────────


def test_full_criteria_accuracy_and_cost():
    p = Promoter()
    result = p.promote(
        candidate_model_id="llama-ft-v3",
        metrics={"accuracy": 0.93, "cost": 0.45},
        criteria="accuracy >= 0.92 AND cost <= 0.50",
    )
    assert result.promoted is True


def test_three_way_and_criteria():
    p = Promoter()
    result = p.promote(
        candidate_model_id="m",
        metrics={"accuracy": 0.93, "f1": 0.91, "latency": 95.0},
        criteria="accuracy >= 0.90 AND f1 >= 0.90 AND latency <= 100",
    )
    assert result.promoted is True

    result2 = p.promote(
        candidate_model_id="m",
        metrics={"accuracy": 0.93, "f1": 0.91, "latency": 105.0},
        criteria="accuracy >= 0.90 AND f1 >= 0.90 AND latency <= 100",
    )
    assert result2.promoted is False
