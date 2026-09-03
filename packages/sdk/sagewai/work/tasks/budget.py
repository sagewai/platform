# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Budget projection from the durable ledger and the Task stream."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import Budget, BudgetUsed, SpendTotals


def worst_case_usd(runtime_name: str, budget: Budget) -> Decimal:
    """Reserve the tier's worst case: Claude is priced, harness is free, Codex is counted."""
    if runtime_name == "claude" or runtime_name.endswith(":runtime.claude"):
        return budget.claude_max_budget_usd_per_attempt
    return Decimal("0")


def budget_used_from(
    totals: SpendTotals, *, events: Sequence[TaskEvent], cycle: int, now: datetime
) -> BudgetUsed:
    """Derive the cycle's usage from the ledger totals and the Task's own events.

    Every counter is per cycle, matching ``Budget``'s per-cycle limits: re-plans proposed in
    an earlier cycle must not spend this cycle's re-plan budget.
    """
    started_at: datetime | None = None
    works = 0
    replans = 0
    current: int | None = None
    for event in sorted(events, key=lambda item: item.sequence):
        if event.event_type is TaskEventType.CYCLE_STARTED:
            current = int(event.payload_json["cycle"])
            if current == cycle:
                started_at = event.created_at
        elif event.event_type is TaskEventType.STEP_WORK_STARTED and current == cycle:
            works += 1
        elif event.event_type is TaskEventType.REPLAN_PROPOSED and current == cycle:
            replans += 1
    return BudgetUsed(
        works=works,
        attempts=totals.reservations,
        replans=replans,
        seconds=int((now - started_at).total_seconds()) if started_at is not None else 0,
        usd_actual=totals.usd_actual,
        usd_reserved=totals.usd_reserved,
        usd_unknown=totals.unknown_settlements,
    )


def budget_breach(used: BudgetUsed, budget: Budget) -> str | None:
    """The first crossed limit, or None."""
    if used.works > budget.max_works_per_cycle:
        return f"works {used.works} exceeds {budget.max_works_per_cycle}"
    if used.attempts > budget.max_stage_attempts_per_cycle:
        return f"attempts {used.attempts} exceeds {budget.max_stage_attempts_per_cycle}"
    if used.replans > budget.max_replans:
        return f"replans {used.replans} exceeds {budget.max_replans}"
    if used.seconds > budget.max_cycle_duration_seconds:
        return f"seconds {used.seconds} exceeds {budget.max_cycle_duration_seconds}"
    spent = used.usd_actual + used.usd_reserved
    if spent > budget.max_cycle_usd:
        return f"usd {spent} exceeds {budget.max_cycle_usd}"
    return None


__all__ = ["budget_breach", "budget_used_from", "worst_case_usd"]
