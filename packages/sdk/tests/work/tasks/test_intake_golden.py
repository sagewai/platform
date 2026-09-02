# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Intake bands against the synthetic golden goals."""

from __future__ import annotations

import pytest

from sagewai.work.tasks.intake import route
from sagewai.work.tasks.models import TaskDefaults

from .golden_goals import GOLDEN_GOALS

DEFAULTS = TaskDefaults(project_id="project-a")
RESERVED_BLUEPRINTS = {"SYNTHETIC_event_triage", "SYNTHETIC_batch_extract"}


@pytest.mark.parametrize(
    "goal,blueprint,blueprint_band,expected_template_id,expected_band",
    GOLDEN_GOALS,
    ids=[g[0][:40] for g in GOLDEN_GOALS],
)
def test_golden_goal_bands(
    goal: str,
    blueprint: str | None,
    blueprint_band: str,
    expected_template_id: str,
    expected_band: str,
) -> None:
    result = route(goal, DEFAULTS)
    assert (result.template_id, result.band) == (expected_template_id, expected_band)
    if blueprint in RESERVED_BLUEPRINTS:
        assert result.band != "auto_route", result


def test_golden_set_size() -> None:
    assert len(GOLDEN_GOALS) == 52
