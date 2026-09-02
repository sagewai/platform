# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task status transition table in the style of admin.autopilot_lifecycle."""

from __future__ import annotations

from sagewai.work.tasks.models import TaskStatus

S = TaskStatus
_ALLOWED: dict[TaskStatus, frozenset[TaskStatus]] = {
    S.PLANNING: frozenset({S.CLARIFYING, S.PLAN_PROPOSED, S.EXECUTING, S.BLOCKED, S.BUDGET_EXHAUSTED, S.PAUSED, S.CANCELLED}),
    S.CLARIFYING: frozenset({S.PLANNING, S.PAUSED, S.CANCELLED}),
    S.PLAN_PROPOSED: frozenset({S.EXECUTING, S.PLANNING, S.BLOCKED, S.PAUSED, S.CANCELLED}),
    S.EXECUTING: frozenset({S.ASSESSING, S.PLANNING, S.BLOCKED, S.BUDGET_EXHAUSTED, S.CONTROL_DEGRADED, S.PAUSED, S.CANCELLED}),
    S.ASSESSING: frozenset({S.COMPLETE, S.SCHEDULED, S.PLANNING, S.BLOCKED, S.BUDGET_EXHAUSTED, S.PAUSED, S.CANCELLED}),
    S.SCHEDULED: frozenset({S.EXECUTING, S.PLANNING, S.PAUSED, S.CANCELLED}),
    S.PAUSED: frozenset({
        S.PLANNING, S.CLARIFYING, S.PLAN_PROPOSED, S.EXECUTING, S.ASSESSING,
        S.SCHEDULED, S.BLOCKED, S.BUDGET_EXHAUSTED, S.CONTROL_DEGRADED, S.CANCELLED,
    }),
    S.BLOCKED: frozenset({S.PLANNING, S.EXECUTING, S.PAUSED, S.CANCELLED}),
    S.BUDGET_EXHAUSTED: frozenset({S.PLANNING, S.EXECUTING, S.PAUSED, S.CANCELLED}),
    S.CONTROL_DEGRADED: frozenset({S.EXECUTING, S.BLOCKED, S.PAUSED, S.CANCELLED}),
    S.COMPLETE: frozenset(),
    S.CANCELLED: frozenset(),
}


class IllegalTransitionError(ValueError):
    """The requested status change is not in the transition table."""


def assert_transition(old: TaskStatus, new: TaskStatus) -> None:
    if new not in _ALLOWED[old]:
        raise IllegalTransitionError(f"cannot move Task from {old.value} to {new.value}")


__all__ = ["IllegalTransitionError", "assert_transition"]
