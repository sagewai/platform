# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Spend is reserved before a metered attempt and settled from the recorded cost."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from sagewai.work.runtime import OperatorResult
from sagewai.work.tasks.budget import BudgetLedger, MeteredOperatorController
from sagewai.work.tasks.models import Budget
from sagewai.work.tasks.store import TaskStore
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _create, _task

NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


class _Claude:
    name = "claude"


class _Codex:
    name = "codex"


@pytest.fixture
async def ledger(dialect_engine):  # noqa: F811
    store = TaskStore(engine=dialect_engine)
    await store.init()
    task = _task()
    await _create(store, task)
    return BudgetLedger(
        store=store, task_id=task.id, project_id=task.project_id, cycle=1, budget=Budget()
    )


@pytest.mark.asyncio
async def test_claude_reserves_its_per_attempt_ceiling_and_settles_the_actual(ledger) -> None:
    await ledger.reserve(run_id="w1:implement:1", stage="implement", runtime=_Claude())
    totals = await ledger.totals()
    assert totals.usd_reserved == Decimal("5.00") and totals.reservations == 1
    await ledger.settle(run_id="w1:implement:1", cost_usd=1.25)
    totals = await ledger.totals()
    assert totals.usd_reserved == Decimal("0") and totals.usd_actual == Decimal("1.25")
    assert ledger.reserved == [("w1:implement:1", "implement", "claude", Decimal("5.00"))]
    assert ledger.settled == [("w1:implement:1", Decimal("1.25"))]


@pytest.mark.asyncio
async def test_codex_reserves_zero_and_settles_unknown(ledger) -> None:
    await ledger.reserve(run_id="w1:implement:1", stage="implement", runtime=_Codex())
    await ledger.settle(run_id="w1:implement:1", cost_usd=None)
    totals = await ledger.totals()
    assert totals.usd_reserved == Decimal("0")
    assert totals.usd_actual == Decimal("0")
    assert totals.unknown_settlements == 1
    assert ledger.settled == [("w1:implement:1", None)]


@pytest.mark.asyncio
async def test_replayed_reserve_and_settle_are_no_ops(ledger) -> None:
    await ledger.reserve(run_id="w1:implement:1", stage="implement", runtime=_Claude())
    await ledger.reserve(run_id="w1:implement:1", stage="implement", runtime=_Claude())
    await ledger.settle(run_id="w1:implement:1", cost_usd=2.0)
    await ledger.settle(run_id="w1:implement:1", cost_usd=2.0)
    totals = await ledger.totals()
    assert totals.reservations == 1 and totals.usd_actual == Decimal("2.0")


@pytest.mark.asyncio
async def test_the_metered_controller_brackets_the_call(ledger, monkeypatch) -> None:
    calls: list[str] = []

    async def fake_run(self, *, runtime, request, capsule, capabilities, workspace):
        calls.append(request.run_id)
        return OperatorResult(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            status="passed",
            summary="done",
            evidence_refs=(),
            artifact_refs=(),
            changes=(),
            verification=(),
            risks=(),
            action_results=(),
            cost_usd=0.5,
        )

    monkeypatch.setattr("sagewai.work.control.OperatorController.run", fake_run)
    controller = MeteredOperatorController(
        ledger=lambda: ledger,
        work_store=None,
        durability_store=None,
        permission_policy=None,
        control_checks={},
        result_validator=None,
    )
    from sagewai.work.models import ActionScope
    from sagewai.work.runtime import WorkRequest

    request = WorkRequest(
        project_id="project-a",
        work_id="w1",
        run_id="w1:implement:1",
        stage="implement",
        action_scope=ActionScope(project_id="project-a", objective="do it", allowed_targets=(".",)),
        action_intents=(),
        control_preconditions=(),
    )
    result = await controller.run(
        runtime=_Claude(), request=request, capsule=None, capabilities=None, workspace=None
    )
    assert result.cost_usd == 0.5
    assert calls == ["w1:implement:1"]
    totals = await ledger.totals()
    assert totals.usd_actual == Decimal("0.5") and totals.usd_reserved == Decimal("0")


@pytest.mark.asyncio
async def test_planning_bills_to_cycle_one_not_cycle_zero(dialect_engine) -> None:  # noqa: F811
    """A reservation written to cycle 0 would never show up in spend_totals(cycle=1)."""
    from sagewai.work.tasks.coordinator import TaskCoordinator
    from tests.work.tasks.test_store import _record

    store = TaskStore(engine=dialect_engine)
    await store.init()
    task = _task()
    await _create(store, task)
    record = _record(task)
    assert record.current_cycle == 0
    assert TaskCoordinator._cycle(record) == 1
    assert TaskCoordinator._cycle(record.model_copy(update={"current_cycle": 4})) == 4
    ledger = BudgetLedger(
        store=store,
        task_id=task.id,
        project_id=task.project_id,
        cycle=TaskCoordinator._cycle(record),
        budget=Budget(),
    )
    run_id = f"{task.id}:plan:1:1:plan:1"
    await ledger.reserve(run_id=run_id, stage="plan", runtime=_Claude())
    await ledger.settle(run_id=run_id, cost_usd=0.75)
    totals = await store.spend_totals(task_id=task.id, project_id=task.project_id, cycle=1)
    assert totals.usd_actual == Decimal("0.75") and totals.reservations == 1
    zero = await store.spend_totals(task_id=task.id, project_id=task.project_id, cycle=0)
    assert zero.reservations == 0
