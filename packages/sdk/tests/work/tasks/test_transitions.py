# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task status transition table."""

from __future__ import annotations

import pytest

from sagewai.work.tasks.models import TaskStatus
from sagewai.work.tasks.transitions import IllegalTransitionError, assert_transition


@pytest.mark.parametrize(
    "old,new",
    [
        (TaskStatus.PLANNING, TaskStatus.CLARIFYING),
        (TaskStatus.PLANNING, TaskStatus.PLAN_PROPOSED),
        (TaskStatus.PLANNING, TaskStatus.EXECUTING),
        (TaskStatus.CLARIFYING, TaskStatus.PLANNING),
        (TaskStatus.PLAN_PROPOSED, TaskStatus.EXECUTING),
        (TaskStatus.EXECUTING, TaskStatus.ASSESSING),
        (TaskStatus.EXECUTING, TaskStatus.PLANNING),
        (TaskStatus.ASSESSING, TaskStatus.COMPLETE),
        (TaskStatus.ASSESSING, TaskStatus.SCHEDULED),
        (TaskStatus.SCHEDULED, TaskStatus.EXECUTING),
        (TaskStatus.EXECUTING, TaskStatus.PAUSED),
        (TaskStatus.PAUSED, TaskStatus.EXECUTING),
        (TaskStatus.BLOCKED, TaskStatus.EXECUTING),
        (TaskStatus.BUDGET_EXHAUSTED, TaskStatus.EXECUTING),
        (TaskStatus.CONTROL_DEGRADED, TaskStatus.EXECUTING),
        (TaskStatus.EXECUTING, TaskStatus.CANCELLED),
    ],
)
def test_allowed_transitions(old: TaskStatus, new: TaskStatus) -> None:
    assert_transition(old, new)


@pytest.mark.parametrize(
    "old,new",
    [
        (TaskStatus.COMPLETE, TaskStatus.EXECUTING),
        (TaskStatus.CANCELLED, TaskStatus.PLANNING),
        (TaskStatus.PLANNING, TaskStatus.COMPLETE),
        (TaskStatus.SCHEDULED, TaskStatus.COMPLETE),
        (TaskStatus.PLANNING, TaskStatus.PLANNING),
    ],
)
def test_illegal_transitions(old: TaskStatus, new: TaskStatus) -> None:
    with pytest.raises(IllegalTransitionError):
        assert_transition(old, new)
