# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sagewai Autopilot eval harness — CI-friendly golden-goal test runner.

Public API surface:

- :class:`GoldenGoal` — a single (goal, expected_blueprint_id, expected_band) fixture.
- :class:`GoldenGoalSet` — a versioned collection of golden goals.
- :class:`EvalReport` — frozen metrics report produced by the harness.
- :class:`EvalConfig` — confidence thresholds to use during a run.
- :class:`EvalHarness` — async orchestrator; use :meth:`EvalHarness.run` synchronously.
- :func:`run_eval` — convenience wrapper: ``run_eval(goal_set)`` → ``EvalReport``.

Synthetic fixture:

- :data:`SYNTHETIC_GOLDEN_GOALS` — 50-goal :class:`GoldenGoalSet` for CI use.
"""

from __future__ import annotations

from .fixtures import SYNTHETIC_GOLDEN_GOALS
from .harness import EvalHarness, run_eval
from .types import EvalConfig, EvalReport, GoldenGoal, GoldenGoalSet

__all__ = [
    "EvalHarness",
    "EvalConfig",
    "EvalReport",
    "GoldenGoal",
    "GoldenGoalSet",
    "SYNTHETIC_GOLDEN_GOALS",
    "run_eval",
]
