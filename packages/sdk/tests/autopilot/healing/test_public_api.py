# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Verify the public API surface of sagewai.autopilot.healing and sagewai.autopilot."""

from __future__ import annotations


class TestHealingSubpackageExports:
    """Every symbol listed in healing/__init__.__all__ must be importable."""

    def test_import_healing_policy(self) -> None:
        from sagewai.autopilot.healing import HealingPolicy
        assert HealingPolicy is not None

    def test_import_rotate_provider(self) -> None:
        from sagewai.autopilot.healing import RotateProvider
        assert RotateProvider is not None

    def test_import_pause_budget(self) -> None:
        from sagewai.autopilot.healing import PauseBudget
        assert PauseBudget is not None

    def test_import_alert_operator(self) -> None:
        from sagewai.autopilot.healing import AlertOperator
        assert AlertOperator is not None

    def test_import_retry_mission(self) -> None:
        from sagewai.autopilot.healing import RetryMission
        assert RetryMission is not None

    def test_import_healing_action(self) -> None:
        from sagewai.autopilot.healing import HealingAction
        assert HealingAction is not None

    def test_import_health_monitor(self) -> None:
        from sagewai.autopilot.healing import HealthMonitor
        assert HealthMonitor is not None

    def test_import_health_signal(self) -> None:
        from sagewai.autopilot.healing import HealthSignal
        assert HealthSignal is not None

    def test_import_healing_engine(self) -> None:
        from sagewai.autopilot.healing import HealingEngine
        assert HealingEngine is not None

    def test_import_mission_context(self) -> None:
        from sagewai.autopilot.healing import MissionContext
        assert MissionContext is not None

    def test_all_list_complete(self) -> None:
        import sagewai.autopilot.healing as mod
        expected = {
            "HealingPolicy",
            "RotateProvider",
            "PauseBudget",
            "AlertOperator",
            "RetryMission",
            "HealingAction",
            "HealthMonitor",
            "HealthSignal",
            "HealingEngine",
            "MissionContext",
        }
        assert expected.issubset(set(mod.__all__))


class TestTopLevelAutopilotExports:
    """Healing symbols must be re-exported from sagewai.autopilot."""

    def test_healing_policy_from_autopilot(self) -> None:
        from sagewai.autopilot import HealingPolicy
        assert HealingPolicy is not None

    def test_rotate_provider_from_autopilot(self) -> None:
        from sagewai.autopilot import RotateProvider
        assert RotateProvider is not None

    def test_pause_budget_from_autopilot(self) -> None:
        from sagewai.autopilot import PauseBudget
        assert PauseBudget is not None

    def test_alert_operator_from_autopilot(self) -> None:
        from sagewai.autopilot import AlertOperator
        assert AlertOperator is not None

    def test_retry_mission_from_autopilot(self) -> None:
        from sagewai.autopilot import RetryMission
        assert RetryMission is not None

    def test_healing_action_from_autopilot(self) -> None:
        from sagewai.autopilot import HealingAction
        assert HealingAction is not None

    def test_health_monitor_from_autopilot(self) -> None:
        from sagewai.autopilot import HealthMonitor
        assert HealthMonitor is not None

    def test_health_signal_from_autopilot(self) -> None:
        from sagewai.autopilot import HealthSignal
        assert HealthSignal is not None

    def test_healing_engine_from_autopilot(self) -> None:
        from sagewai.autopilot import HealingEngine
        assert HealingEngine is not None

    def test_mission_context_from_autopilot(self) -> None:
        from sagewai.autopilot import MissionContext
        assert MissionContext is not None

    def test_healing_symbols_in_all(self) -> None:
        import sagewai.autopilot as mod
        healing_symbols = {
            "HealingPolicy",
            "RotateProvider",
            "PauseBudget",
            "AlertOperator",
            "RetryMission",
            "HealingAction",
            "HealthMonitor",
            "HealthSignal",
            "HealingEngine",
            "MissionContext",
        }
        assert healing_symbols.issubset(set(mod.__all__))

    def test_prior_exports_still_present(self) -> None:
        """Ensure Plan 8 additions did not break existing __all__ entries."""
        import sagewai.autopilot as mod
        prior = {
            "AutopilotController",
            "MissionDriver",
            "MissionRunResult",
            "StepResult",
            "EvalHarness",
            "Curator",
            "Blueprint",
            "Mission",
        }
        assert prior.issubset(set(mod.__all__))
