# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""TaskPlanResult validation and deterministic acceptance."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.work.models import ProposedAcceptanceCriterion
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.intake import ClarificationQuestion
from sagewai.work.tasks.models import Budget, ReportTarget, SoftwareTarget
from sagewai.work.tasks.plan import (
    AcceptedPlan,
    MatrixItem,
    PlanRejectedError,
    PlanStep,
    TaskPlanResult,
    accept_plan,
    clarification_request_entry,
    plan_rules,
    proposed_plan_from_events,
)

NOW = datetime(2026, 9, 6, 11, 0, tzinfo=timezone.utc)
TARGET = SoftwareTarget(repository_path="/repo", owner="o", repo="r", verification_image="sha256:" + "a" * 64)


def _criterion() -> ProposedAcceptanceCriterion:
    return ProposedAcceptanceCriterion(statement="tests pass", verification_kind="deterministic")


def _step(step_id: str, depends_on: tuple[str, ...] = (), scope: tuple[str, ...] = ("app/",)) -> PlanStep:
    return PlanStep(
        id=step_id, title=step_id, goal=f"do {step_id}", allowed_scope=scope,
        acceptance_criteria=(_criterion(),), constraints=(), non_goals=(), risk="low",
        design_required=False, depends_on=depends_on, domain="backend", size="s",
    )


def _matrix(command: str | None = "just smoke") -> tuple[MatrixItem, ...]:
    return (
        MatrixItem(
            id="m1",
            statement="verification passes",
            verification_kind="deterministic",
            command=command,
        ),
        MatrixItem(id="m2", statement="brief satisfied", verification_kind="policy"),
    )


def _result(steps=(), matrix=None, clarifications=()) -> TaskPlanResult:
    return TaskPlanResult(
        attempt_id="task-1:plan:1:1:plan:1",
        steps=tuple(steps),
        acceptance_matrix=_matrix() if matrix is None else matrix,
        clarifications=tuple(clarifications),
        claims=(),
    )


def _event(sequence: int, event_type: TaskEventType, payload: dict) -> TaskEvent:
    return TaskEvent(
        id=f"event-{sequence}",
        project_id="project-a",
        task_id="task-1",
        sequence=sequence,
        event_type=event_type,
        actor_type="system",
        actor_ref="test",
        payload_json=payload,
        created_at=NOW,
    )


def test_plan_rules_name_the_shape_each_target_needs() -> None:
    target = TARGET.model_copy(update={"verification_commands": ("just smoke", "just test")})
    software_rules = plan_rules(target)
    report_rules = plan_rules(ReportTarget(required_sections=("Summary",)))

    assert "deterministic" in " ".join(software_rules)
    for command in target.verification_commands:
        assert repr(command) in " ".join(software_rules)
    assert "never instruct delivery" in " ".join(software_rules)
    assert "pull request" in " ".join(software_rules)
    assert "report.md" in " ".join(report_rules)
    assert "policy" in " ".join(report_rules)
    assert "pull request" not in " ".join(report_rules)


def test_a_rejected_scope_says_what_a_scope_is() -> None:
    with pytest.raises(PlanRejectedError) as excinfo:
        accept_plan(
            _result(steps=(_step("a", scope=(".",)),)),
            budget=Budget(),
            target=TARGET,
            version=1,
        )
    assert "relative to the checkout" in str(excinfo.value)

    with pytest.raises(PlanRejectedError) as excinfo:
        accept_plan(
            _result(steps=(_step("r", scope=(".",)),), matrix=(_matrix()[1],)),
            budget=Budget(),
            target=ReportTarget(required_sections=("Summary",)),
            version=1,
        )
    assert "report.md" in str(excinfo.value)


def test_accept_plan_orders_steps_topologically() -> None:
    result = _result(steps=(_step("b", depends_on=("a",)), _step("a"), _step("c", depends_on=("a", "b"))))
    accepted = accept_plan(result, budget=Budget(), target=TARGET, version=1)
    assert isinstance(accepted, AcceptedPlan)
    assert [step.id for step in accepted.steps] == ["a", "b", "c"]
    assert accepted.version == 1


@pytest.mark.parametrize(
    "steps,reason",
    [
        ((_step("a", depends_on=("b",)), _step("b", depends_on=("a",))), "cycle"),
        ((_step("a", depends_on=("zzz",)),), "unknown dependency"),
        ((_step("a", depends_on=("a",)),), "cycle"),
        ((_step("a", scope=(".",)),), "not surgical"),
        ((_step("a", scope=("/etc",)),), "not surgical"),
        ((_step("a", scope=("../x",)),), "not surgical"),
        ((_step("a", scope=("./.",)),), "not surgical"),
        ((_step("a", scope=(" /etc",)),), "not surgical"),
        ((_step("a"), _step("a")), "duplicate step"),
    ],
)
def test_accept_plan_rejects_bad_graphs_and_scopes(steps, reason) -> None:
    with pytest.raises(PlanRejectedError) as excinfo:
        accept_plan(_result(steps=steps), budget=Budget(), target=TARGET, version=1)
    assert reason in str(excinfo.value)


def test_accept_plan_rejects_empty_criteria_and_too_many_steps() -> None:
    with pytest.raises(Exception):
        PlanStep(
            id="a", title="a", goal="g", allowed_scope=("app/",), acceptance_criteria=(),
            constraints=(), non_goals=(), risk="low", design_required=False, depends_on=(),
            domain="backend", size="s",
        )
    too_many = tuple(_step(f"s{i}") for i in range(13))
    with pytest.raises(PlanRejectedError) as excinfo:
        accept_plan(_result(steps=too_many), budget=Budget(), target=TARGET, version=1)
    assert "budget" in str(excinfo.value)


def test_accept_plan_matrix_rules_for_software_targets() -> None:
    with pytest.raises(PlanRejectedError) as excinfo:
        accept_plan(_result(steps=(_step("a"),), matrix=(_matrix()[1],)), budget=Budget(), target=TARGET, version=1)
    assert "deterministic" in str(excinfo.value)
    with pytest.raises(PlanRejectedError) as excinfo:
        accept_plan(_result(steps=(_step("a"),), matrix=_matrix(command="rm -rf /")), budget=Budget(), target=TARGET, version=1)
    assert "locked verification" in str(excinfo.value)


def test_accept_plan_rejects_empty_and_duplicate_matrix() -> None:
    with pytest.raises(PlanRejectedError) as excinfo:
        accept_plan(_result(steps=(_step("a"),), matrix=()), budget=Budget(), target=TARGET, version=1)
    assert "empty" in str(excinfo.value)
    duplicate = (_matrix()[0], _matrix()[0].model_copy(update={"statement": "again"}))
    with pytest.raises(PlanRejectedError) as excinfo:
        accept_plan(_result(steps=(_step("a"),), matrix=duplicate), budget=Budget(), target=TARGET, version=1)
    assert "duplicate" in str(excinfo.value)


def test_accept_plan_report_target_allows_policy_only_matrix() -> None:
    target = ReportTarget(required_sections=("Summary",))
    accepted = accept_plan(_result(steps=(_step("r"),), matrix=(_matrix()[1],)), budget=Budget(), target=target, version=1)
    assert [item.id for item in accepted.acceptance_matrix] == ["m2"]
    with pytest.raises(PlanRejectedError):
        accept_plan(_result(steps=(_step("r"),), matrix=_matrix()), budget=Budget(), target=target, version=1)


def test_a_plan_may_carry_defaultable_clarifications() -> None:
    defaultable = ClarificationQuestion(id="q", text="?", defaultable=True, default="x")
    material = ClarificationQuestion(id="m", text="?", defaultable=False)
    attached = _result(steps=(_step("a"),), clarifications=(defaultable,))
    assert not attached.asks_first
    accepted = accept_plan(attached, budget=Budget(), target=TARGET, version=1)
    assert [step.id for step in accepted.steps] == ["a"]
    with pytest.raises(ValueError):
        _result(steps=(_step("a"),), clarifications=(material,))
    with pytest.raises(ValueError):
        _result(
            steps=(_step("a"),),
            clarifications=(ClarificationQuestion(id="q", text="?", defaultable=True),),
        )
    asking = _result(clarifications=(material,))
    assert asking.asks_first
    with pytest.raises(PlanRejectedError) as excinfo:
        accept_plan(asking, budget=Budget(), target=TARGET, version=1)
    assert "clarifications" in str(excinfo.value)


def test_a_clarification_request_states_the_open_counts() -> None:
    open_q1 = _event(
        1,
        TaskEventType.CLARIFICATION_REQUESTED,
        {
            "questions": [
                {
                    "id": "q1",
                    "defaultable": True,
                    "default": "x",
                    "attention_version": 1,
                }
            ],
            "deadline_at": None,
            "pending_questions": 1,
            "pending_material_questions": 0,
        },
    )
    entry = clarification_request_entry(
        [open_q1],
        (
            ClarificationQuestion(
                id="q1",
                text="again",
                defaultable=True,
                default="x",
                attention_version=2,
            ),
            ClarificationQuestion(id="q2", text="new", defaultable=False),
        ),
        deadline_at=datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc),
    )
    assert entry[0] is TaskEventType.CLARIFICATION_REQUESTED
    assert entry[1]["pending_questions"] == 2
    assert entry[1]["pending_material_questions"] == 1
    assert entry[1]["deadline_at"] == "2026-09-06T12:00:00+00:00"


def test_the_latest_proposal_wins() -> None:
    first = {
        "version": 1,
        "steps": [_step("s1").model_dump(mode="json")],
        "acceptance_matrix": [item.model_dump(mode="json") for item in _matrix()],
    }
    second = {
        "version": 2,
        "steps": [_step("s2").model_dump(mode="json")],
        "acceptance_matrix": [item.model_dump(mode="json") for item in _matrix()],
    }

    proposed = proposed_plan_from_events(
        (
            _event(1, TaskEventType.PLAN_PROPOSED, first),
            _event(2, TaskEventType.PLAN_PROPOSED, second),
        )
    )

    assert proposed is not None
    assert proposed.version == 2
    assert proposed.steps[0].id == "s2"


def test_matrix_speaks_the_kernel_verification_vocabulary() -> None:
    judged = MatrixItem(id="m2", statement="brief satisfied", verification_kind="policy")
    assert judged.command is None
    for gone in ("assessment", "profile"):
        with pytest.raises(ValueError):
            MatrixItem(id="m3", statement="brief satisfied", verification_kind=gone)
    accepted = accept_plan(
        _result(steps=(_step("a"),), matrix=(_matrix()[0], judged)),
        budget=Budget(),
        target=TARGET,
        version=1,
    )
    assert [item.verification_kind for item in accepted.acceptance_matrix] == [
        "deterministic",
        "policy",
    ]
