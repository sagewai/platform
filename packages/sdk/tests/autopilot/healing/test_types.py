# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for sagewai.autopilot.healing.types."""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from sagewai.autopilot.healing.types import (
    AlertOperator,
    HealingAction,
    HealingPolicy,
    PauseBudget,
    RetryMission,
    RotateProvider,
)

# ---------------------------------------------------------------------------
# HealingPolicy
# ---------------------------------------------------------------------------


class TestHealingPolicy:
    def test_defaults(self) -> None:
        p = HealingPolicy()
        assert p.failure_threshold == 3
        assert p.cost_spike_multiplier == 2.0
        assert p.success_rate_window == 20
        assert p.success_rate_minimum == 0.8
        assert p.duration_spike_multiplier == 3.0

    def test_custom_values(self) -> None:
        p = HealingPolicy(
            failure_threshold=5,
            cost_spike_multiplier=1.5,
            success_rate_window=10,
            success_rate_minimum=0.9,
            duration_spike_multiplier=2.5,
        )
        assert p.failure_threshold == 5
        assert p.cost_spike_multiplier == 1.5
        assert p.success_rate_window == 10
        assert p.success_rate_minimum == 0.9
        assert p.duration_spike_multiplier == 2.5

    def test_frozen(self) -> None:
        p = HealingPolicy()
        with pytest.raises(ValidationError):
            p.failure_threshold = 99  # type: ignore[misc]

    def test_failure_threshold_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            HealingPolicy(failure_threshold=0)

    def test_cost_spike_multiplier_must_exceed_one(self) -> None:
        with pytest.raises(ValidationError):
            HealingPolicy(cost_spike_multiplier=1.0)

    def test_duration_spike_multiplier_must_exceed_one(self) -> None:
        with pytest.raises(ValidationError):
            HealingPolicy(duration_spike_multiplier=0.5)

    def test_success_rate_minimum_bounds(self) -> None:
        with pytest.raises(ValidationError):
            HealingPolicy(success_rate_minimum=-0.1)
        with pytest.raises(ValidationError):
            HealingPolicy(success_rate_minimum=1.1)

    def test_success_rate_window_minimum(self) -> None:
        with pytest.raises(ValidationError):
            HealingPolicy(success_rate_window=1)

    def test_json_roundtrip(self) -> None:
        p = HealingPolicy(failure_threshold=5)
        assert HealingPolicy.model_validate_json(p.model_dump_json()) == p


# ---------------------------------------------------------------------------
# RotateProvider
# ---------------------------------------------------------------------------


class TestRotateProvider:
    def test_construction(self) -> None:
        a = RotateProvider(blueprint_id="bp-1")
        assert a.kind == "rotate_provider"
        assert a.blueprint_id == "bp-1"
        assert a.suggested_provider == "fallback"

    def test_custom_provider(self) -> None:
        a = RotateProvider(blueprint_id="bp-1", suggested_provider="openai")
        assert a.suggested_provider == "openai"

    def test_frozen(self) -> None:
        a = RotateProvider(blueprint_id="bp-1")
        with pytest.raises(ValidationError):
            a.blueprint_id = "other"  # type: ignore[misc]

    def test_blueprint_id_required(self) -> None:
        with pytest.raises(ValidationError):
            RotateProvider(blueprint_id="")

    def test_json_roundtrip(self) -> None:
        a = RotateProvider(blueprint_id="bp-1", suggested_provider="anthropic")
        assert RotateProvider.model_validate_json(a.model_dump_json()) == a


# ---------------------------------------------------------------------------
# PauseBudget
# ---------------------------------------------------------------------------


class TestPauseBudget:
    def test_construction(self) -> None:
        a = PauseBudget(mission_id="m-1", reason="cost exceeded")
        assert a.kind == "pause_budget"
        assert a.mission_id == "m-1"
        assert a.reason == "cost exceeded"

    def test_frozen(self) -> None:
        a = PauseBudget(mission_id="m-1", reason="x")
        with pytest.raises(ValidationError):
            a.mission_id = "other"  # type: ignore[misc]

    def test_requires_mission_id(self) -> None:
        with pytest.raises(ValidationError):
            PauseBudget(mission_id="", reason="x")

    def test_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            PauseBudget(mission_id="m-1", reason="")

    def test_json_roundtrip(self) -> None:
        a = PauseBudget(mission_id="m-1", reason="spike")
        assert PauseBudget.model_validate_json(a.model_dump_json()) == a


# ---------------------------------------------------------------------------
# AlertOperator
# ---------------------------------------------------------------------------


class TestAlertOperator:
    def test_construction_defaults(self) -> None:
        a = AlertOperator(message="something wrong")
        assert a.kind == "alert_operator"
        assert a.severity == "warning"

    def test_severity_variants(self) -> None:
        for sev in ("info", "warning", "critical"):
            a = AlertOperator(message="msg", severity=sev)  # type: ignore[arg-type]
            assert a.severity == sev

    def test_invalid_severity(self) -> None:
        with pytest.raises(ValidationError):
            AlertOperator(message="msg", severity="fatal")  # type: ignore[arg-type]

    def test_requires_message(self) -> None:
        with pytest.raises(ValidationError):
            AlertOperator(message="")

    def test_frozen(self) -> None:
        a = AlertOperator(message="x")
        with pytest.raises(ValidationError):
            a.message = "y"  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        a = AlertOperator(message="alert!", severity="critical")
        assert AlertOperator.model_validate_json(a.model_dump_json()) == a


# ---------------------------------------------------------------------------
# RetryMission
# ---------------------------------------------------------------------------


class TestRetryMission:
    def test_construction_defaults(self) -> None:
        a = RetryMission(mission_id="m-1")
        assert a.kind == "retry_mission"
        assert a.backoff_seconds == 30.0

    def test_custom_backoff(self) -> None:
        a = RetryMission(mission_id="m-1", backoff_seconds=60.0)
        assert a.backoff_seconds == 60.0

    def test_backoff_cannot_be_negative(self) -> None:
        with pytest.raises(ValidationError):
            RetryMission(mission_id="m-1", backoff_seconds=-1.0)

    def test_requires_mission_id(self) -> None:
        with pytest.raises(ValidationError):
            RetryMission(mission_id="")

    def test_frozen(self) -> None:
        a = RetryMission(mission_id="m-1")
        with pytest.raises(ValidationError):
            a.mission_id = "other"  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        a = RetryMission(mission_id="m-1", backoff_seconds=45.0)
        assert RetryMission.model_validate_json(a.model_dump_json()) == a


# ---------------------------------------------------------------------------
# HealingAction discriminated union
# ---------------------------------------------------------------------------


class TestHealingActionUnion:
    """Verify that the discriminated union deserialises correctly."""

    _adapter: TypeAdapter[HealingAction] = TypeAdapter(HealingAction)

    def _round(self, raw: dict) -> HealingAction:
        return self._adapter.validate_python(raw)

    def test_rotate_provider_discriminated(self) -> None:
        raw = {"kind": "rotate_provider", "blueprint_id": "bp-1"}
        action = self._round(raw)
        assert isinstance(action, RotateProvider)

    def test_pause_budget_discriminated(self) -> None:
        raw = {"kind": "pause_budget", "mission_id": "m-1", "reason": "spike"}
        action = self._round(raw)
        assert isinstance(action, PauseBudget)

    def test_alert_operator_discriminated(self) -> None:
        raw = {"kind": "alert_operator", "message": "alert!", "severity": "critical"}
        action = self._round(raw)
        assert isinstance(action, AlertOperator)

    def test_retry_mission_discriminated(self) -> None:
        raw = {"kind": "retry_mission", "mission_id": "m-1"}
        action = self._round(raw)
        assert isinstance(action, RetryMission)

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValidationError):
            self._round({"kind": "explode", "mission_id": "m-1"})

    def test_json_roundtrip_all_variants(self) -> None:
        actions: list[HealingAction] = [
            RotateProvider(blueprint_id="bp-1"),
            PauseBudget(mission_id="m-1", reason="x"),
            AlertOperator(message="y"),
            RetryMission(mission_id="m-2"),
        ]
        for action in actions:
            serialised = json.loads(action.model_dump_json())
            recovered = self._adapter.validate_python(serialised)
            assert type(recovered) is type(action)
