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
RESEARCH = "SYNTHETIC_scheduled_research"


@pytest.mark.parametrize("goal,blueprint,band", GOLDEN_GOALS, ids=[g[0][:40] for g in GOLDEN_GOALS])
def test_golden_goal_bands(goal: str, blueprint: str, band: str) -> None:
    result = route(goal, DEFAULTS)
    if blueprint == RESEARCH and band == "auto_route":
        assert result.template_id == "scheduled_research_report"
        assert result.band == "auto_route", result
    else:
        # Triage, extract, picker, and synthesis goals must never auto-route to the report template.
        assert not (result.template_id == "scheduled_research_report" and result.band == "auto_route"), result


def test_golden_set_size() -> None:
    assert len(GOLDEN_GOALS) == 52
