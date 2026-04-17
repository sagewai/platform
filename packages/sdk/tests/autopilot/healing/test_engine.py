"""Tests for sagewai.autopilot.healing.engine.HealingEngine."""

from __future__ import annotations

import pytest

from sagewai.autopilot.healing import (
    AlertOperator,
    HealingEngine,
    HealingPolicy,
    HealthMonitor,
    MissionContext,
    PauseBudget,
    RetryMission,
    RotateProvider,
)

from .conftest import make_failed, make_ok, make_result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_engine(policy: HealingPolicy | None = None) -> HealingEngine:
    p = policy or HealingPolicy(failure_threshold=3, cost_spike_multiplier=2.0)
    return HealingEngine(monitor=HealthMonitor(policy=p), policy=p)


def strict_engine() -> HealingEngine:
    p = HealingPolicy(
        failure_threshold=2,
        cost_spike_multiplier=1.5,
        success_rate_window=4,
        success_rate_minimum=0.75,
        duration_spike_multiplier=2.0,
    )
    return HealingEngine(monitor=HealthMonitor(policy=p), policy=p)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestHealingEngineConstruction:
    def test_default_policy_from_monitor(self) -> None:
        monitor = HealthMonitor()
        engine = HealingEngine(monitor=monitor)
        assert engine._policy.failure_threshold == 3

    def test_explicit_policy_overrides(self) -> None:
        monitor = HealthMonitor()
        p = HealingPolicy(failure_threshold=5)
        engine = HealingEngine(monitor=monitor, policy=p)
        assert engine._policy.failure_threshold == 5

    def test_mismatched_contexts_raises(self) -> None:
        engine = make_engine()
        with pytest.raises(ValueError, match="contexts length"):
            engine.evaluate(
                results=[make_ok(), make_ok()],
                contexts=[MissionContext(blueprint_id="bp-1")],
            )


# ---------------------------------------------------------------------------
# Clean run — no actions
# ---------------------------------------------------------------------------


class TestCleanRun:
    def test_empty_results_no_actions(self) -> None:
        engine = make_engine()
        assert engine.evaluate([]) == []

    def test_successful_missions_no_actions(self) -> None:
        engine = make_engine()
        results = [make_ok(f"m-{i}") for i in range(10)]
        contexts = [MissionContext(blueprint_id="bp-1") for _ in results]
        assert engine.evaluate(results, contexts) == []

    def test_single_failure_no_actions(self) -> None:
        # Below failure_threshold=3
        engine = make_engine()
        ctx = MissionContext(blueprint_id="bp-1")
        assert engine.evaluate([make_failed()], [ctx]) == []

    def test_two_failures_no_actions_default_threshold(self) -> None:
        engine = make_engine()
        results = [make_failed(f"m-{i}") for i in range(2)]
        contexts = [MissionContext(blueprint_id="bp-1") for _ in results]
        assert engine.evaluate(results, contexts) == []


# ---------------------------------------------------------------------------
# Provider failure → RotateProvider
# ---------------------------------------------------------------------------


class TestProviderFailureAction:
    def test_rotate_at_threshold(self) -> None:
        engine = strict_engine()  # threshold=2
        results = [make_failed("m-1"), make_failed("m-2")]
        contexts = [MissionContext(blueprint_id="bp-A") for _ in results]
        actions = engine.evaluate(results, contexts)
        rotations = [a for a in actions if isinstance(a, RotateProvider)]
        assert len(rotations) == 1
        assert rotations[0].blueprint_id == "bp-A"

    def test_rotate_default_provider_is_fallback(self) -> None:
        engine = strict_engine()
        results = [make_failed(f"m-{i}") for i in range(2)]
        contexts = [MissionContext(blueprint_id="bp-A") for _ in results]
        actions = engine.evaluate(results, contexts)
        rotate = next(a for a in actions if isinstance(a, RotateProvider))
        assert rotate.suggested_provider == "fallback"

    def test_no_duplicate_rotate_for_same_blueprint(self) -> None:
        engine = strict_engine()
        # 5 failures — should still produce exactly one RotateProvider
        results = [make_failed(f"m-{i}") for i in range(5)]
        contexts = [MissionContext(blueprint_id="bp-A") for _ in results]
        actions = engine.evaluate(results, contexts)
        rotations = [a for a in actions if isinstance(a, RotateProvider)]
        assert len(rotations) == 1

    def test_separate_blueprints_separate_rotations(self) -> None:
        engine = strict_engine()
        results = [
            make_failed("m-1"),
            make_failed("m-2"),
            make_failed("m-3"),
            make_failed("m-4"),
        ]
        contexts = [
            MissionContext(blueprint_id="bp-A"),
            MissionContext(blueprint_id="bp-A"),
            MissionContext(blueprint_id="bp-B"),
            MissionContext(blueprint_id="bp-B"),
        ]
        actions = engine.evaluate(results, contexts)
        rotations = [a for a in actions if isinstance(a, RotateProvider)]
        blueprint_ids = {r.blueprint_id for r in rotations}
        assert blueprint_ids == {"bp-A", "bp-B"}

    def test_success_after_failures_resets_no_rotation(self) -> None:
        engine = strict_engine()
        results = [make_failed("m-1"), make_ok("m-2")]
        contexts = [MissionContext(blueprint_id="bp-A") for _ in results]
        actions = engine.evaluate(results, contexts)
        assert not any(isinstance(a, RotateProvider) for a in actions)


# ---------------------------------------------------------------------------
# Cost spike → PauseBudget + AlertOperator(warning)
# ---------------------------------------------------------------------------


class TestCostSpikeActions:
    def test_cost_spike_produces_pause_and_alert(self) -> None:
        engine = strict_engine()  # multiplier=1.5
        result = make_ok("m-spike")
        ctx = MissionContext(blueprint_id="bp-1", estimated_cost=1.0, actual_cost=2.0)
        actions = engine.evaluate([result], [ctx])
        assert any(isinstance(a, PauseBudget) for a in actions)
        alerts = [a for a in actions if isinstance(a, AlertOperator)]
        assert any(a.severity == "warning" for a in alerts)

    def test_pause_budget_has_correct_mission_id(self) -> None:
        engine = strict_engine()
        result = make_ok("m-spike")
        ctx = MissionContext(blueprint_id="bp-1", estimated_cost=1.0, actual_cost=2.0)
        actions = engine.evaluate([result], [ctx])
        pauses = [a for a in actions if isinstance(a, PauseBudget)]
        assert pauses[0].mission_id == "m-spike"

    def test_no_cost_spike_when_cost_zero(self) -> None:
        engine = strict_engine()
        result = make_ok("m-1")
        ctx = MissionContext(blueprint_id="bp-1", estimated_cost=0.0, actual_cost=999.0)
        actions = engine.evaluate([result], [ctx])
        assert not any(isinstance(a, PauseBudget) for a in actions)

    def test_no_spike_below_multiplier(self) -> None:
        engine = strict_engine()
        result = make_ok("m-1")
        ctx = MissionContext(blueprint_id="bp-1", estimated_cost=1.0, actual_cost=1.4)
        actions = engine.evaluate([result], [ctx])
        assert not any(isinstance(a, PauseBudget) for a in actions)

    def test_no_cost_spike_without_context(self) -> None:
        # No contexts provided — cost checks skipped
        engine = strict_engine()
        result = make_ok("m-1")
        actions = engine.evaluate([result])
        assert not any(isinstance(a, PauseBudget) for a in actions)

    def test_no_duplicate_pause_for_same_mission(self) -> None:
        engine = strict_engine()
        results = [make_ok("m-same"), make_ok("m-same")]
        contexts = [
            MissionContext(blueprint_id="bp-1", estimated_cost=1.0, actual_cost=2.0),
            MissionContext(blueprint_id="bp-1", estimated_cost=1.0, actual_cost=2.0),
        ]
        actions = engine.evaluate(results, contexts)
        pauses = [a for a in actions if isinstance(a, PauseBudget)]
        assert len(pauses) == 1


# ---------------------------------------------------------------------------
# Drift → AlertOperator(critical)
# ---------------------------------------------------------------------------


class TestDriftActions:
    def test_drift_produces_critical_alert(self) -> None:
        engine = strict_engine()  # window=4, minimum=0.75
        # 2 successes, 2 failures → rate = 0.5 < 0.75
        results = [make_ok("m-1"), make_ok("m-2"), make_failed("m-3"), make_failed("m-4")]
        contexts = [MissionContext(blueprint_id="bp-drift") for _ in results]
        actions = engine.evaluate(results, contexts)
        critical_alerts = [a for a in actions if isinstance(a, AlertOperator) and a.severity == "critical"]
        assert len(critical_alerts) == 1

    def test_no_drift_above_minimum(self) -> None:
        engine = strict_engine()
        # 3 successes, 1 failure → rate = 0.75 which is NOT < 0.75
        results = [make_ok("m-1"), make_ok("m-2"), make_ok("m-3"), make_failed("m-4")]
        contexts = [MissionContext(blueprint_id="bp-clean") for _ in results]
        actions = engine.evaluate(results, contexts)
        critical_alerts = [a for a in actions if isinstance(a, AlertOperator) and a.severity == "critical"]
        assert len(critical_alerts) == 0

    def test_no_drift_before_window_full(self) -> None:
        engine = strict_engine()
        # Only 3 failures (window=4 not yet full)
        results = [make_failed(f"m-{i}") for i in range(3)]
        contexts = [MissionContext(blueprint_id="bp-short") for _ in results]
        actions = engine.evaluate(results, contexts)
        critical_alerts = [a for a in actions if isinstance(a, AlertOperator) and a.severity == "critical"]
        assert len(critical_alerts) == 0

    def test_no_duplicate_drift_alert_same_blueprint(self) -> None:
        engine = strict_engine()
        # 8 results (2 windows), all failures
        results = [make_failed(f"m-{i}") for i in range(8)]
        contexts = [MissionContext(blueprint_id="bp-d") for _ in results]
        actions = engine.evaluate(results, contexts)
        critical_alerts = [a for a in actions if isinstance(a, AlertOperator) and a.severity == "critical"]
        assert len(critical_alerts) == 1


# ---------------------------------------------------------------------------
# Timeout → RetryMission + AlertOperator(warning)
# ---------------------------------------------------------------------------


class TestTimeoutActions:
    def test_timeout_produces_retry_and_alert(self) -> None:
        engine = strict_engine()  # multiplier=2.0
        result = make_result("m-slow", status="failed", duration_seconds=25.0)
        ctx = MissionContext(blueprint_id="bp-1", estimated_duration=10.0)
        actions = engine.evaluate([result], [ctx])
        retries = [a for a in actions if isinstance(a, RetryMission)]
        assert len(retries) == 1
        assert retries[0].mission_id == "m-slow"
        assert retries[0].backoff_seconds == 30.0
        alerts = [a for a in actions if isinstance(a, AlertOperator) and a.severity == "warning"]
        assert len(alerts) >= 1

    def test_no_timeout_below_threshold(self) -> None:
        engine = strict_engine()
        result = make_ok("m-fast")
        ctx = MissionContext(blueprint_id="bp-1", estimated_duration=10.0)
        # actual duration = 1.0 (from make_ok default)
        actions = engine.evaluate([result], [ctx])
        assert not any(isinstance(a, RetryMission) for a in actions)

    def test_no_timeout_without_estimated_duration(self) -> None:
        engine = strict_engine()
        result = make_result("m-1", duration_seconds=999.0)
        ctx = MissionContext(blueprint_id="bp-1", estimated_duration=0.0)
        actions = engine.evaluate([result], [ctx])
        assert not any(isinstance(a, RetryMission) for a in actions)

    def test_no_duplicate_retry_same_mission(self) -> None:
        engine = strict_engine()
        results = [
            make_result("m-same", duration_seconds=25.0),
            make_result("m-same", duration_seconds=25.0),
        ]
        contexts = [MissionContext(blueprint_id="bp-1", estimated_duration=10.0) for _ in results]
        actions = engine.evaluate(results, contexts)
        retries = [a for a in actions if isinstance(a, RetryMission)]
        assert len(retries) == 1


# ---------------------------------------------------------------------------
# Combined multi-signal scenario
# ---------------------------------------------------------------------------


class TestCombinedScenario:
    def test_all_action_types_in_one_run(self) -> None:
        """A pathological batch that triggers all four detection rules."""
        engine = strict_engine()

        # 2 failures → provider failure for bp-main
        fail_results = [make_failed("m-fail-1"), make_failed("m-fail-2")]
        fail_ctxs = [MissionContext(blueprint_id="bp-main") for _ in fail_results]

        # 1 cost spike
        spike_result = make_ok("m-spike")
        spike_ctx = MissionContext(blueprint_id="bp-main", estimated_cost=1.0, actual_cost=2.0)

        # Fill window to trigger drift (2 ok + 2 fail = 0.5 rate < 0.75)
        drift_results = [
            make_ok("m-d1"), make_ok("m-d2"),
            make_failed("m-d3"), make_failed("m-d4"),
        ]
        drift_ctxs = [MissionContext(blueprint_id="bp-drift") for _ in drift_results]

        # 1 timeout
        timeout_result = make_result("m-timeout", duration_seconds=25.0)
        timeout_ctx = MissionContext(blueprint_id="bp-main", estimated_duration=10.0)

        all_results = fail_results + [spike_result] + drift_results + [timeout_result]
        all_ctxs = fail_ctxs + [spike_ctx] + drift_ctxs + [timeout_ctx]

        actions = engine.evaluate(all_results, all_ctxs)

        kinds = {type(a).__name__ for a in actions}
        assert "RotateProvider" in kinds
        assert "PauseBudget" in kinds
        assert "AlertOperator" in kinds
        assert "RetryMission" in kinds

        critical = [a for a in actions if isinstance(a, AlertOperator) and a.severity == "critical"]
        assert len(critical) >= 1
