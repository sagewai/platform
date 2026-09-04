# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""decide() returns exactly the first applicable command of spec section 8.3."""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from sagewai.work.tasks.budget import budget_breach, budget_used_from, worst_case_usd
from sagewai.work.tasks.decide import (
    AssessCycle,
    BlockCycle,
    CompleteCycle,
    DeliverReport,
    ExhaustBudget,
    MirrorAttention,
    RecordStepOutcome,
    Replan,
    RequestDeliverGate,
    ResumeStep,
    RollbackWork,
    RunPlanning,
    StartCycle,
    StartStep,
    StepWorkState,
    SupersedeStep,
    decide,
    fold_cycle,
)
from sagewai.work.tasks.events import TaskEventType, fold_record
from sagewai.work.tasks.models import (
    Budget,
    BudgetUsed,
    Schedule,
    SpendTotals,
    TaskKind,
    TaskStatus,
)
from sagewai.work.tasks.writer import build_events
from tests.work.tasks.test_store import NOW, _record, _task


def _step(step_id: str, *, depends_on: list[str]) -> dict:
    return {
        "id": step_id,
        "title": f"Step {step_id}",
        "goal": f"Deliver {step_id}",
        "allowed_scope": ["src"],
        "acceptance_criteria": [
            {"statement": "the suite passes", "verification_kind": "deterministic"}
        ],
        "constraints": [],
        "non_goals": [],
        "risk": "low",
        "design_required": False,
        "depends_on": depends_on,
        "domain": "backend",
        "size": "s",
    }


STEPS = [_step("s1", depends_on=[]), _step("s2", depends_on=["s1"])]
MATRIX = [
    {
        "id": "m1",
        "statement": "just smoke passes",
        "verification_kind": "deterministic",
        "command": "just smoke",
    }
]


def _apply(record, entries, *, now=NOW):
    events = build_events(record, entries, actor_type="system", actor_ref="coordinator", now=now)
    return fold_record(record, events), list(events)


def _planned(task, *, cycle: int = 1):
    """A Task with an accepted plan and cycle ``cycle`` started."""
    record = _record(task)
    record, events = _apply(
        record,
        [
            (
                TaskEventType.PLAN_PROPOSED,
                {"version": 1, "steps": STEPS, "acceptance_matrix": MATRIX},
            ),
            (TaskEventType.PLAN_ACCEPTED, {"version": 1}),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.EXECUTING.value}),
            (TaskEventType.CYCLE_STARTED, {"cycle": cycle, "scheduled_for": None}),
        ],
    )
    return record, events


def _extend(record, events, entries, *, now=NOW):
    record, more = _apply(record, entries, now=now)
    return record, [*events, *more]


def test_planning_status_runs_the_planner() -> None:
    task = _task()
    record = _record(task)
    assert decide(task, record, [], {}, budget_used=BudgetUsed(), now=NOW) == RunPlanning(
        plan_version=1
    )


def test_waiting_statuses_and_a_pending_gate_yield_no_command() -> None:
    task = _task()
    record = _record(task).model_copy(update={"status": TaskStatus.BLOCKED})
    assert decide(task, record, [], {}, budget_used=BudgetUsed(), now=NOW) is None
    record = _record(task).model_copy(
        update={"status": TaskStatus.EXECUTING, "pending_gate": "plan:task-1:1"}
    )
    assert decide(task, record, [], {}, budget_used=BudgetUsed(), now=NOW) is None


@pytest.mark.parametrize(
    ("used", "state", "expected"),
    [
        (
            BudgetUsed(works=99),
            StepWorkState(step_id="s1", work_id="w1", status="COMPLETE", merged_sha="c" * 40),
            ExhaustBudget(reason="works 99 exceeds 12"),
        ),
        (
            BudgetUsed(),
            StepWorkState(step_id="s1", work_id="w1", status="COMPLETE", merged_sha="c" * 40),
            RecordStepOutcome(
                step_id="s1", work_id="w1", outcome="accepted", merged_sha="c" * 40
            ),
        ),
        (
            BudgetUsed(),
            StepWorkState(
                step_id="s1",
                work_id="w1",
                status="WORK_BLOCKED",
                attention_kind="WORK_BLOCKED",
                attention_id="w1:blocked",
                attention_summary="needs a decision",
            ),
            MirrorAttention(
                step_id="s1",
                work_id="w1",
                attention_kind="WORK_BLOCKED",
                attention_id="w1:blocked",
                summary="needs a decision",
            ),
        ),
        (
            BudgetUsed(),
            StepWorkState(step_id="s1", work_id="w1", status="BASE_MOVED", base_moved_phase="publish"),
            SupersedeStep(step_id="s1", work_id="w1", phase="publish"),
        ),
        (
            BudgetUsed(),
            StepWorkState(step_id="s1", work_id="w1", status="IMPLEMENTING"),
            ResumeStep(step_id="s1", work_id="w1"),
        ),
        (BudgetUsed(), None, StartStep(step_id="s1")),
    ],
)
def test_command_order_follows_section_8_3(used, state, expected) -> None:
    task = _task()
    record, events = _planned(task)
    works = {}
    if state is not None:
        record, events = _extend(
            record,
            events,
            [
                (
                    TaskEventType.STEP_WORK_STARTED,
                    {
                        "step_id": "s1",
                        "work_id": "w1",
                        "issue_url": "u",
                        "base_sha": "a" * 40,
                    },
                )
            ],
        )
        works = {"s1": state}
    assert record.current_cycle == 1
    assert decide(task, record, events, works, budget_used=used, now=NOW) == expected


def test_dependencies_gate_the_next_step_and_assessment_follows_the_last_outcome() -> None:
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w1", "issue_url": "u", "base_sha": "a" * 40},
            ),
            (
                TaskEventType.STEP_WORK_OUTCOME,
                {"step_id": "s1", "work_id": "w1", "outcome": "accepted"},
            ),
        ],
    )
    assert decide(task, record, events, {}, budget_used=BudgetUsed(), now=NOW) == StartStep(
        step_id="s2"
    )
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s2", "work_id": "w2", "issue_url": "u2", "base_sha": "a" * 40},
            ),
            (
                TaskEventType.STEP_WORK_OUTCOME,
                {"step_id": "s2", "work_id": "w2", "outcome": "accepted"},
            ),
        ],
    )
    assert decide(task, record, events, {}, budget_used=BudgetUsed(), now=NOW) == AssessCycle(
        cycle=1
    )


def test_replanned_cycle_reassesses_the_new_accepted_plan() -> None:
    task = _task()
    record, events = _planned(task)
    for step, work in (("s1", "w1"), ("s2", "w2")):
        record, events = _extend(
            record,
            events,
            [
                (
                    TaskEventType.STEP_WORK_STARTED,
                    {
                        "step_id": step,
                        "work_id": work,
                        "issue_url": f"https://github.test/o/r/issues/{work}",
                        "base_sha": "a" * 40,
                    },
                ),
                (
                    TaskEventType.STEP_WORK_OUTCOME,
                    {"step_id": step, "work_id": work, "outcome": "accepted"},
                ),
            ],
        )
    record, events = _extend(
        record,
        events,
        [
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.ASSESSING.value}),
            (
                TaskEventType.ASSESSMENT_RECORDED,
                {"cycle": 1, "verdict": "replan", "matrix_results": [], "gaps": []},
            ),
        ],
    )
    assert decide(task, record, events, {}, budget_used=BudgetUsed(), now=NOW) == Replan(
        plan_version=2, reason="assessment requested a re-plan"
    )
    record, events = _extend(
        record,
        events,
        [
            (TaskEventType.REPLAN_PROPOSED, {"version": 2, "reason": "gap"}),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.PLANNING.value}),
        ],
    )
    assert decide(task, record, events, {}, budget_used=BudgetUsed(replans=1), now=NOW) == RunPlanning(
        plan_version=2
    )
    steps = [*STEPS, _step("s3", depends_on=["s2"])]
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.PLAN_PROPOSED,
                {"version": 2, "steps": steps, "acceptance_matrix": MATRIX},
            ),
            (TaskEventType.PLAN_ACCEPTED, {"version": 2}),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.EXECUTING.value}),
            (
                TaskEventType.STEP_WORK_STARTED,
                {
                    "step_id": "s3",
                    "work_id": "w3",
                    "issue_url": "https://github.test/o/r/issues/3",
                    "base_sha": "b" * 40,
                },
            ),
            (
                TaskEventType.STEP_WORK_OUTCOME,
                {"step_id": "s3", "work_id": "w3", "outcome": "accepted"},
            ),
        ],
    )
    assert decide(task, record, events, {}, budget_used=BudgetUsed(replans=1), now=NOW) == AssessCycle(
        cycle=1
    )


@pytest.mark.parametrize(
    ("state", "expected_type"),
    [
        (
            StepWorkState(
                step_id="s1",
                work_id="w1",
                status="COMPLETE",
                merged_sha="c" * 40,
                attention_kind="WORK_BLOCKED",
                attention_id="w1:blocked",
                attention_summary="stale block",
            ),
            RecordStepOutcome,
        ),
        (
            StepWorkState(
                step_id="s1",
                work_id="w1",
                status="BASE_MOVED",
                attention_kind="WORK_BLOCKED",
                attention_id="w1:blocked",
                attention_summary="blocked",
                base_moved_phase="merge",
            ),
            MirrorAttention,
        ),
        (
            StepWorkState(
                step_id="s1",
                work_id="w1",
                status="COMPLETE",
                merged_sha="c" * 40,
                base_moved_phase="merge",
            ),
            RecordStepOutcome,
        ),
    ],
)
def test_adjacent_branch_precedence_when_work_fields_overlap(state, expected_type) -> None:
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w1", "issue_url": "u", "base_sha": "a" * 40},
            )
        ],
    )
    assert isinstance(
        decide(task, record, events, {"s1": state}, budget_used=BudgetUsed(), now=NOW),
        expected_type,
    )


@pytest.mark.parametrize("event_type", [TaskEventType.GATE_REQUESTED, TaskEventType.TASK_MESSAGE])
def test_mirrored_attention_ids_are_suppressed_but_new_ids_mirror_again(event_type) -> None:
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w1", "issue_url": "u", "base_sha": "a" * 40},
            )
        ],
    )
    if event_type is TaskEventType.GATE_REQUESTED:
        entries = [
            (
                TaskEventType.GATE_REQUESTED,
                {
                    "gate_id": "merge:w1",
                    "question": "Merge?",
                    "action": {},
                    "work_id": "w1",
                    "attention_id": "w1:blocked",
                },
            ),
            (TaskEventType.GATE_DECIDED, {"gate_id": "merge:w1", "decision": "allow"}),
        ]
    else:
        entries = [
            (
                TaskEventType.TASK_MESSAGE,
                {
                    "author": "coordinator",
                    "text": "blocked",
                    "refs": ["w1"],
                    "attention_id": "w1:blocked",
                },
            )
        ]
    record, events = _extend(record, events, entries)
    blocked = StepWorkState(
        step_id="s1",
        work_id="w1",
        status="WORK_BLOCKED",
        attention_kind="WORK_BLOCKED",
        attention_id="w1:blocked",
        attention_summary="needs a decision",
    )
    assert decide(task, record, events, {"s1": blocked}, budget_used=BudgetUsed(), now=NOW) is None
    fresh = blocked.model_copy(update={"attention_id": "w1:other"})
    assert decide(task, record, events, {"s1": fresh}, budget_used=BudgetUsed(), now=NOW) == MirrorAttention(
        step_id="s1",
        work_id="w1",
        attention_kind="WORK_BLOCKED",
        attention_id="w1:other",
        summary="needs a decision",
    )


def test_plan_accepted_without_a_cycle_starts_cycle_one() -> None:
    task = _task()
    record = _record(task)
    record, events = _apply(
        record,
        [
            (
                TaskEventType.PLAN_PROPOSED,
                {"version": 1, "steps": STEPS, "acceptance_matrix": MATRIX},
            ),
            (TaskEventType.PLAN_ACCEPTED, {"version": 1}),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.EXECUTING.value}),
        ],
    )
    assert record.current_cycle == 0
    assert decide(task, record, events, {}, budget_used=BudgetUsed(), now=NOW) == StartCycle(cycle=1)


def test_allowed_rollback_runs_once_and_delivery_results_do_not_count() -> None:
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w1", "issue_url": "u", "base_sha": "a" * 40},
            ),
            (
                TaskEventType.ACTION_RESULT_RECORDED,
                {"action_id": "deliver:w1:1", "work_id": "w1", "status": "succeeded"},
            ),
            (TaskEventType.GATE_DECIDED, {"gate_id": "rollback:w1", "decision": "allow"}),
        ],
    )

    state = fold_cycle(events, plan_version=record.plan_version)
    assert state.decided_gates == {"rollback:w1": "allow"}
    assert state.delivered == frozenset({"deliver:w1:1"})
    assert state.rolled_back == frozenset()
    assert decide(task, record, events, {}, budget_used=BudgetUsed(), now=NOW) == RollbackWork(
        work_id="w1"
    )
    pending_record, pending_events = record, events

    for action_id in ("revert:w1:7", "delete_comment:w1:123"):
        record, events = _extend(
            pending_record,
            pending_events,
            [
                (
                    TaskEventType.ACTION_RESULT_RECORDED,
                    {"action_id": action_id, "work_id": "w1", "status": "succeeded"},
                )
            ],
        )

        state = fold_cycle(events, plan_version=record.plan_version)
        assert state.delivered == frozenset({"deliver:w1:1"})
        assert state.rolled_back == frozenset({"w1"})
        assert decide(
            task, record, events, {}, budget_used=BudgetUsed(), now=NOW
        ) != RollbackWork(work_id="w1")


def test_delivery_uses_the_pending_sink_version_after_an_earlier_sink_delivered() -> None:
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w1", "issue_url": "u", "base_sha": "a" * 40},
            ),
            (TaskEventType.GATE_DECIDED, {"gate_id": "deliver:w1:1", "decision": "allow"}),
            (
                TaskEventType.ACTION_RESULT_RECORDED,
                {"action_id": "deliver:w1:1", "work_id": "w1", "status": "succeeded"},
            ),
        ],
    )
    work = StepWorkState(
        step_id="s1",
        work_id="w1",
        status="READY_TO_DELIVER",
        deliver_sink_version=2,
    )
    assert decide(task, record, events, {"s1": work}, budget_used=BudgetUsed(), now=NOW) == (
        RequestDeliverGate(step_id="s1", work_id="w1", sink_version=2)
    )
    record, events = _extend(
        record,
        events,
        [(TaskEventType.GATE_DECIDED, {"gate_id": "deliver:w1:2", "decision": "allow"})],
    )
    assert decide(task, record, events, {"s1": work}, budget_used=BudgetUsed(), now=NOW) == (
        DeliverReport(step_id="s1", work_id="w1", sink_version=2)
    )


def test_a_denied_rollback_gate_is_not_executed() -> None:
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w1", "issue_url": "u", "base_sha": "a" * 40},
            ),
            (TaskEventType.GATE_DECIDED, {"gate_id": "rollback:w1", "decision": "deny"}),
        ],
    )

    state = fold_cycle(events, plan_version=record.plan_version)
    assert state.decided_gates == {"rollback:w1": "deny"}
    assert state.rolled_back == frozenset()
    assert decide(task, record, events, {}, budget_used=BudgetUsed(), now=NOW) != RollbackWork(
        work_id="w1"
    )

def test_assessment_verdicts_complete_replan_or_block() -> None:
    task = _task()
    record, events = _planned(task)
    for step, work in (("s1", "w1"), ("s2", "w2")):
        record, events = _extend(
            record,
            events,
            [
                (
                    TaskEventType.STEP_WORK_STARTED,
                    {
                        "step_id": step,
                        "work_id": work,
                        "issue_url": "u",
                        "base_sha": "a" * 40,
                    },
                ),
                (
                    TaskEventType.STEP_WORK_OUTCOME,
                    {"step_id": step, "work_id": work, "outcome": "accepted"},
                ),
            ],
        )
    accepted, accepted_events = _extend(
        record,
        events,
        [
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.ASSESSING.value}),
            (
                TaskEventType.ASSESSMENT_RECORDED,
                {"cycle": 1, "verdict": "accept", "matrix_results": [], "gaps": []},
            ),
        ],
    )
    assert decide(task, accepted, accepted_events, {}, budget_used=BudgetUsed(), now=NOW) == CompleteCycle(
        cycle=1, outcome="succeeded", next_run_at=None
    )
    replan, replan_events = _extend(
        record,
        events,
        [
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.ASSESSING.value}),
            (
                TaskEventType.ASSESSMENT_RECORDED,
                {
                    "cycle": 1,
                    "verdict": "replan",
                    "matrix_results": [],
                    "gaps": [
                        {"statement": "m1 unmet", "severity": "high", "suggested_step": "retry"}
                    ],
                },
            ),
        ],
    )
    assert decide(task, replan, replan_events, {}, budget_used=BudgetUsed(), now=NOW) == Replan(
        plan_version=2, reason="assessment requested a re-plan"
    )
    assert decide(
        task, replan, replan_events, {}, budget_used=BudgetUsed(replans=2), now=NOW
    ) == BlockCycle(reason="assessment verdict replan with the re-plan budget spent")
    blocked, blocked_events = _extend(
        record,
        events,
        [
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.ASSESSING.value}),
            (
                TaskEventType.ASSESSMENT_RECORDED,
                {"cycle": 1, "verdict": "blocked", "matrix_results": [], "gaps": []},
            ),
        ],
    )
    assert decide(task, blocked, blocked_events, {}, budget_used=BudgetUsed(), now=NOW) == BlockCycle(
        reason="assessment verdict blocked"
    )


def test_scheduled_completion_carries_next_fire_in_utc() -> None:
    task = _task().model_copy(
        update={
            "kind": TaskKind.SCHEDULED,
            "schedule": Schedule(cron="0 8 * * *", timezone="Europe/Berlin"),
        }
    )
    record, events = _planned(task)
    for step, work in (("s1", "w1"), ("s2", "w2")):
        record, events = _extend(
            record,
            events,
            [
                (
                    TaskEventType.STEP_WORK_STARTED,
                    {"step_id": step, "work_id": work, "issue_url": "u", "base_sha": "a" * 40},
                ),
                (
                    TaskEventType.STEP_WORK_OUTCOME,
                    {"step_id": step, "work_id": work, "outcome": "accepted"},
                ),
            ],
        )
    record, events = _extend(
        record,
        events,
        [
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.ASSESSING.value}),
            (
                TaskEventType.ASSESSMENT_RECORDED,
                {"cycle": 1, "verdict": "accept", "matrix_results": [], "gaps": []},
            ),
        ],
    )
    assert decide(task, record, events, {}, budget_used=BudgetUsed(), now=NOW) == CompleteCycle(
        cycle=1, outcome="succeeded", next_run_at="2026-09-03T06:00:00+00:00"
    )


def test_a_due_scheduled_task_starts_the_next_cycle() -> None:
    task = _task()
    record = _record(task).model_copy(
        update={
            "status": TaskStatus.SCHEDULED,
            "current_cycle": 3,
            "next_run_at": NOW - timedelta(minutes=1),
        }
    )
    assert decide(task, record, [], {}, budget_used=BudgetUsed(), now=NOW) == StartCycle(
        cycle=4, scheduled_for=(NOW - timedelta(minutes=1)).isoformat()
    )
    early = record.model_copy(update={"next_run_at": NOW + timedelta(minutes=1)})
    assert decide(task, early, [], {}, budget_used=BudgetUsed(), now=NOW) is None


def test_the_budget_is_checked_first_but_only_where_the_status_allows_it() -> None:
    """Section 8.3 step 1, bounded by the transition table: SCHEDULED has no BUDGET_EXHAUSTED edge."""
    task = _task()
    spent = BudgetUsed(works=99)
    planning = _record(task).model_copy(update={"current_cycle": 1, "plan_version": 1})
    assert decide(task, planning, [], {}, budget_used=spent, now=NOW) == ExhaustBudget(
        reason="works 99 exceeds 12"
    )
    first = _record(task)
    assert decide(task, first, [], {}, budget_used=spent, now=NOW) == RunPlanning(plan_version=1)
    scheduled = _record(task).model_copy(
        update={
            "status": TaskStatus.SCHEDULED,
            "current_cycle": 3,
            "next_run_at": NOW - timedelta(minutes=1),
        }
    )
    assert decide(task, scheduled, [], {}, budget_used=spent, now=NOW) == StartCycle(
        cycle=4, scheduled_for=(NOW - timedelta(minutes=1)).isoformat()
    )
    plan_proposed = _record(task).model_copy(
        update={"status": TaskStatus.PLAN_PROPOSED, "current_cycle": 1}
    )
    assert decide(task, plan_proposed, [], {}, budget_used=spent, now=NOW) is None
    clarifying = _record(task).model_copy(update={"status": TaskStatus.CLARIFYING, "current_cycle": 1})
    assert decide(task, clarifying, [], {}, budget_used=spent, now=NOW) is None


def test_unaccepted_dependency_blocks_the_ready_step_and_assesses_instead() -> None:
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w1", "issue_url": "u", "base_sha": "a" * 40},
            ),
            (
                TaskEventType.STEP_WORK_OUTCOME,
                {"step_id": "s1", "work_id": "w1", "outcome": "rejected"},
            ),
        ],
    )
    assert decide(task, record, events, {}, budget_used=BudgetUsed(), now=NOW) == AssessCycle(
        cycle=1
    )


def test_replans_are_counted_per_cycle() -> None:
    task = _task()
    record, events = _planned(task, cycle=1)
    record, events = _extend(
        record, events, [(TaskEventType.REPLAN_PROPOSED, {"version": 2, "reason": "gap"})]
    )
    record, events = _extend(
        record, events, [(TaskEventType.CYCLE_STARTED, {"cycle": 2, "scheduled_for": None})]
    )
    totals = SpendTotals(
        usd_reserved=Decimal("0"), usd_actual=Decimal("0"), unknown_settlements=0, reservations=0
    )
    assert budget_used_from(totals, events=events, cycle=1, now=NOW).replans == 1
    assert budget_used_from(totals, events=events, cycle=2, now=NOW).replans == 0


def test_fold_cycle_replay_idempotence_order_independence_and_issue_urls() -> None:
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {
                    "step_id": "s1",
                    "work_id": "w1",
                    "issue_url": "https://github.test/o/r/issues/1",
                    "base_sha": "a" * 40,
                },
            ),
            (
                TaskEventType.STEP_WORK_OUTCOME,
                {"step_id": "s1", "work_id": "w1", "outcome": "accepted"},
            ),
        ],
    )
    state = fold_cycle(events, plan_version=1)
    shuffled = list(events)
    random.Random(7).shuffle(shuffled)
    assert state == fold_cycle([*events, *events], plan_version=1)
    assert state == fold_cycle(shuffled, plan_version=1)
    assert state.issue_urls == {"s1": "https://github.test/o/r/issues/1"}


def test_fold_cycle_resets_per_cycle_state_on_a_second_cycle_start() -> None:
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w1", "issue_url": "u", "base_sha": "a" * 40},
            ),
            (
                TaskEventType.STEP_WORK_SUPERSEDED,
                {
                    "step_id": "s1",
                    "work_id": "w1",
                    "superseded_by": "w9",
                    "reason": "base_moved",
                },
            ),
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w9", "issue_url": "u", "base_sha": "b" * 40},
            ),
            (
                TaskEventType.STEP_WORK_OUTCOME,
                {"step_id": "s1", "work_id": "w9", "outcome": "accepted"},
            ),
            (
                TaskEventType.ASSESSMENT_RECORDED,
                {"cycle": 1, "verdict": "accept", "matrix_results": [], "gaps": []},
            ),
            (
                TaskEventType.TASK_MESSAGE,
                {"author": "coordinator", "text": "x", "refs": [], "attention_id": "old"},
            ),
            (TaskEventType.CYCLE_STARTED, {"cycle": 2, "scheduled_for": None}),
        ],
    )
    mid = fold_cycle(list(events)[:-1], plan_version=1)
    assert mid.step_works == {"s1": "w9"}
    assert mid.superseded_works == frozenset({"w1"})
    state = fold_cycle(events, plan_version=1)
    assert state.step_works == {}
    assert state.issue_urls == {}
    assert state.step_outcomes == {}
    assert state.assessment is None
    assert state.mirrored == frozenset()
    assert state.superseded_works == frozenset()


def test_budget_projection_and_breach() -> None:
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w1", "issue_url": "u", "base_sha": "a" * 40},
            )
        ],
    )
    totals = SpendTotals(
        usd_reserved=Decimal("5"), usd_actual=Decimal("2"), unknown_settlements=1, reservations=4
    )
    used = budget_used_from(totals, events=events, cycle=1, now=NOW + timedelta(seconds=90))
    assert used == BudgetUsed(
        works=1,
        attempts=4,
        replans=0,
        seconds=90,
        usd_actual=Decimal("2"),
        usd_reserved=Decimal("5"),
        usd_unknown=1,
    )
    assert budget_breach(used, Budget()) is None
    assert budget_breach(used, Budget(max_cycle_usd=Decimal("6"))) == "usd 7 exceeds 6"
    assert worst_case_usd("claude", Budget()) == Decimal("5.00")
    assert worst_case_usd("harness", Budget()) == Decimal("0")
    assert worst_case_usd("fleet:org-1:runtime.codex", Budget()) == Decimal("0")


def test_budget_breach_checks_attempts_replans_seconds_and_first_crossed_limit() -> None:
    assert budget_breach(BudgetUsed(attempts=61), Budget()) == "attempts 61 exceeds 60"
    assert budget_breach(BudgetUsed(replans=3), Budget()) == "replans 3 exceeds 2"
    assert budget_breach(BudgetUsed(seconds=28801), Budget()) == "seconds 28801 exceeds 28800"
    assert (
        budget_breach(BudgetUsed(works=13, attempts=61), Budget())
        == "works 13 exceeds 12"
    )


def test_budget_used_counts_works_per_cycle_and_seconds_from_requested_cycle() -> None:
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w1", "issue_url": "u1", "base_sha": "a" * 40},
            ),
            (TaskEventType.REPLAN_PROPOSED, {"version": 2, "reason": "gap"}),
        ],
    )
    record, events = _extend(
        record,
        events,
        [(TaskEventType.CYCLE_STARTED, {"cycle": 2, "scheduled_for": None})],
        now=NOW + timedelta(seconds=10),
    )
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s2", "work_id": "w2", "issue_url": "u2", "base_sha": "b" * 40},
            ),
            (TaskEventType.REPLAN_PROPOSED, {"version": 3, "reason": "gap2"}),
        ],
        now=NOW + timedelta(seconds=10),
    )
    totals = SpendTotals(
        usd_reserved=Decimal("0"), usd_actual=Decimal("0"), unknown_settlements=0, reservations=7
    )
    cycle_1 = budget_used_from(totals, events=events, cycle=1, now=NOW + timedelta(seconds=60))
    cycle_2 = budget_used_from(totals, events=events, cycle=2, now=NOW + timedelta(seconds=60))
    assert (cycle_1.works, cycle_2.works) == (1, 1)
    assert (cycle_1.replans, cycle_2.replans) == (1, 1)
    assert (cycle_1.seconds, cycle_2.seconds) == (60, 50)
    assert cycle_1.attempts == 7


def test_every_command_receipt_id_is_kind_and_revision() -> None:
    commands = [
        StartCycle(cycle=1),
        RunPlanning(plan_version=1),
        ExhaustBudget(reason="r"),
        RecordStepOutcome(step_id="s", work_id="w", outcome="accepted"),
        MirrorAttention(
            step_id="s",
            work_id="w",
            attention_kind="WORK_BLOCKED",
            attention_id="a",
            summary="blocked",
        ),
        SupersedeStep(step_id="s", work_id="w", phase="merge"),
        StartStep(step_id="s"),
        ResumeStep(step_id="s", work_id="w"),
        AssessCycle(cycle=1),
        Replan(plan_version=2, reason="r"),
        BlockCycle(reason="r"),
        CompleteCycle(cycle=1, outcome="succeeded"),
    ]
    assert [command.receipt_id(7) for command in commands] == [
        f"{command.kind}:7" for command in commands
    ]


def test_decide_rejects_mirrored_attention_without_an_attention_id() -> None:
    with pytest.raises(ValidationError):
        MirrorAttention(
            step_id="s1",
            work_id="w1",
            attention_kind="WORK_BLOCKED",
            attention_id=None,
            summary="missing id",
        )
    task = _task()
    record, events = _planned(task)
    record, events = _extend(
        record,
        events,
        [
            (
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "s1", "work_id": "w1", "issue_url": "u", "base_sha": "a" * 40},
            )
        ],
    )
    with pytest.raises(ValidationError):
        decide(
            task,
            record,
            events,
            {
                "s1": StepWorkState(
                    step_id="s1",
                    work_id="w1",
                    status="WORK_BLOCKED",
                    attention_kind="WORK_BLOCKED",
                    attention_summary="missing id",
                )
            },
            budget_used=BudgetUsed(),
            now=NOW,
        )


def test_worst_case_usd_only_prices_claude_runtime_names() -> None:
    assert worst_case_usd("claude", Budget()) == Decimal("5.00")
    assert worst_case_usd("fleet:org-1:runtime.claude", Budget()) == Decimal("5.00")
    assert worst_case_usd("fleet:claude-org:runtime.codex", Budget()) == Decimal("0")
