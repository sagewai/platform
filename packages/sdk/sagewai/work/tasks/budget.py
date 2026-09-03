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

from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sagewai.work.control import OperatorController
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import Budget, BudgetUsed, SpendTotals
from sagewai.work.tasks.store import SpendReservation, TaskStore


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


class BudgetLedger:
    """One cycle's durable spend: reserve the worst case, settle the recorded cost."""

    def __init__(
        self, *, store: TaskStore, task_id: str, project_id: str, cycle: int, budget: Budget
    ) -> None:
        self._store = store
        self._task_id = task_id
        self._project_id = project_id
        self._cycle = cycle
        self._budget = budget
        self.reserved: list[tuple[str, str, str, Decimal]] = []
        self.settled: list[tuple[str, Decimal | None]] = []

    @property
    def task_id(self) -> str:
        return self._task_id

    async def reserve(self, *, run_id: str, stage: str, runtime: Any) -> None:
        usd = worst_case_usd(runtime.name, self._budget)
        try:
            await self._store.reserve_spend(
                SpendReservation(
                    reservation_id=run_id,
                    project_id=self._project_id,
                    task_id=self._task_id,
                    cycle=self._cycle,
                    role=stage,
                    runtime=runtime.name,
                    usd_reserved=usd,
                )
            )
        except ValueError:
            return
        self.reserved.append((run_id, stage, runtime.name, usd))

    async def settle(self, *, run_id: str, cost_usd: float | None) -> None:
        usd = None if cost_usd is None else Decimal(str(cost_usd))
        try:
            await self._store.settle_spend(run_id, project_id=self._project_id, usd_actual=usd)
        except KeyError:
            return
        self.settled.append((run_id, usd))

    async def totals(self) -> SpendTotals:
        return await self._store.spend_totals(
            task_id=self._task_id, project_id=self._project_id, cycle=self._cycle
        )

    def drain(self) -> list[tuple[TaskEventType, dict[str, Any]]]:
        """The SPEND_RESERVED and SPEND_SETTLED entries written since the last drain."""
        entries: list[tuple[TaskEventType, dict[str, Any]]] = [
            (
                TaskEventType.SPEND_RESERVED,
                {
                    "reservation_id": run_id,
                    "role": role,
                    "runtime": runtime,
                    "usd_reserved": str(usd),
                },
            )
            for run_id, role, runtime, usd in self.reserved
        ]
        entries.extend(
            (
                TaskEventType.SPEND_SETTLED,
                {"reservation_id": run_id, "usd_actual": None if usd is None else str(usd)},
            )
            for run_id, usd in self.settled
        )
        self.reserved.clear()
        self.settled.clear()
        return entries


class MeteredOperatorController(OperatorController):
    """Bracket every stage attempt with a durable reservation and settlement.

    ``ledger`` is a callable because one cached stack serves many cycles.
    """

    def __init__(self, *, ledger: Callable[[], BudgetLedger], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._ledger = ledger

    async def run(self, *, runtime, request, capsule, capabilities, workspace):
        ledger = self._ledger()
        await ledger.reserve(run_id=request.run_id, stage=request.stage, runtime=runtime)
        result = await super().run(
            runtime=runtime,
            request=request,
            capsule=capsule,
            capabilities=capabilities,
            workspace=workspace,
        )
        await ledger.settle(run_id=request.run_id, cost_usd=result.cost_usd)
        return result


__all__ = [
    "BudgetLedger",
    "MeteredOperatorController",
    "budget_breach",
    "budget_used_from",
    "worst_case_usd",
]
