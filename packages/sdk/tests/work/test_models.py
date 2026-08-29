# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the generic Work-domain value objects."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import sagewai.work as work
from sagewai.work import (
    Action,
    ActionIntent,
    ActionResult,
    ActionScope,
    Assumption,
    ClaimClassification,
    ControlPrecondition,
    ControlPreconditionKind,
    OperatorDisciplineReport,
    Reversibility,
    ReviewFinding,
    ReviewResult,
    VerificationResult,
    WorkContract,
    WorkEventType,
    WorkItem,
    WorkRecord,
)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _contract(**updates) -> WorkContract:
    values = {
        "id": "contract-1",
        "project_id": "project-a",
        "work_id": "work-1",
        "version": 1,
        "goal": "Add the generic Work domain",
        "allowed_scope": ("packages/sdk/sagewai/work",),
        "acceptance_criteria": ("the store is append-only",),
        "constraints": ("reuse the database layer",),
        "non_goals": ("GitHub integration",),
        "evidence_refs": ("repo://base/AGENTS.md",),
        "assumption_ids": (),
        "risk": "low",
        "design_required": False,
        "profile_context": {"opaque": {"base_sha": "not-kernel-data"}},
        "supersedes": None,
    }
    values.update(updates)
    return WorkContract.model_validate(values)


def test_work_item_is_frozen_and_project_scoped() -> None:
    item = WorkItem(
        id="work-1",
        project_id="project-a",
        profile="software",
        source="local",
        source_ref=None,
        title="Work domain",
        description="Implement PR 1",
        target_systems=("repository",),
        created_at=NOW,
    )

    assert item.project_id == "project-a"
    with pytest.raises(ValidationError):
        item.title = "mutated"  # type: ignore[misc]


def test_claim_classification_is_exact() -> None:
    assert {item.value for item in ClaimClassification} == {
        "FACT",
        "REQUIREMENT",
        "INFERENCE",
        "DECISION",
        "UNKNOWN",
    }


def test_control_event_types_are_first_class_work_events() -> None:
    assert WorkEventType.CONTROL_DEGRADED.value == "CONTROL_DEGRADED"
    assert WorkEventType.CONTROL_RESTORED.value == "CONTROL_RESTORED"


def test_work_contract_versions_are_immutable_and_linked() -> None:
    first = _contract()
    second = _contract(
        id="contract-2",
        version=2,
        goal="Add the generic Work domain and persistence",
        supersedes=first.id,
    )

    assert first.version == 1
    assert first.supersedes is None
    assert second.version == 2
    assert second.supersedes == first.id
    with pytest.raises(ValidationError):
        first.version = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _contract(version=0)


def test_profile_context_round_trips_without_interpretation() -> None:
    contract = _contract()

    restored = WorkContract.model_validate_json(contract.model_dump_json())

    assert restored == contract
    assert restored.profile_context == {"opaque": {"base_sha": "not-kernel-data"}}


def test_assumption_verification_and_review_models_are_typed_and_immutable() -> None:
    assumption = Assumption(
        id="assumption-1",
        project_id="project-a",
        statement="A compatibility path is required",
        kind="compatibility",
        evidence_refs=(),
        confidence="low",
        impact_if_wrong="high",
        status="open",
    )
    verification = VerificationResult(
        attempt_id="verify-1",
        passed=False,
        evidence_refs=("knowledge-verification-1",),
        profile_context={"checks": [{"command": "just smoke", "exit_code": 1}]},
    )
    finding = ReviewFinding(
        severity="high",
        claim="The compatibility path is unsupported",
        evidence_refs=("knowledge-verification-1",),
        required_change="Remove the unsupported compatibility path",
        profile_context={"file": "target.py", "line": 12},
    )
    review = ReviewResult(
        attempt_id="review-1",
        verdict="repair",
        findings=(finding,),
        evidence_refs=("review://review-1",),
        introduced_assumptions=("A compatibility path is required",),
        unsupported_claims=("The compatibility path is supported",),
        scope_expansions=("Support legacy callers",),
        unsupported_implementation_choices=("backward compatibility",),
    )

    assert verification.passed is False
    assert review.findings == (finding,)
    assert review.introduced_assumptions == ("A compatibility path is required",)
    with pytest.raises(ValidationError):
        assumption.status = "validated"  # type: ignore[misc]


def test_review_requires_every_semantic_independent_check_answer() -> None:
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(
            {
                "attempt_id": "review-1",
                "verdict": "accept",
                "findings": [],
                "evidence_refs": ["review://review-1"],
            }
        )


def test_review_schema_asks_the_four_semantic_independent_check_questions() -> None:
    properties = ReviewResult.model_json_schema()["properties"]

    assert {
        name: properties[name]["description"]
        for name in (
            "introduced_assumptions",
            "unsupported_claims",
            "scope_expansions",
            "unsupported_implementation_choices",
        )
    } == {
        "introduced_assumptions": "What new assumptions were introduced?",
        "unsupported_claims": (
            "Which claims are unsupported by the WorkItem, repo evidence, accepted "
            "contract, or project policy?"
        ),
        "scope_expansions": "Did implementation solve a wider problem than requested?",
        "unsupported_implementation_choices": (
            "Was backward compatibility, migration, fallback, abstraction, or defensive "
            "behavior added without evidence?"
        ),
    }


def test_action_and_discipline_models_match_the_generic_contract() -> None:
    scope = ActionScope(
        project_id="project-a",
        objective="Implement PR 1",
        allowed_targets=("packages/sdk/sagewai/work",),
        max_files_changed=10,
        max_diff_lines=500,
        allowed_capabilities=("filesystem", "pytest"),
    )
    action = Action(
        id="action-1",
        project_id="project-a",
        work_id="work-1",
        profile="software",
        target_system="repository",
        capability="filesystem.write",
        scope=scope.model_dump(mode="json"),
        inputs={"paths": ["packages/sdk/sagewai/work"]},
        expected_effect="Work-domain files exist",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        preconditions=("base_sha_matches",),
        verification=("focused_tests_pass",),
    )
    intent = ActionIntent(
        project_id="project-a",
        action_id=action.id,
        capability=action.capability,
        target=action.target_system,
        expected_effect=action.expected_effect,
        scope=action.scope,
        risk="low",
        reversibility=action.reversibility,
        required_permission="workspace.write",
        evidence_refs=("decision://pr-1",),
    )
    result = ActionResult(
        project_id="project-a",
        action_id=action.id,
        status="succeeded",
        external_ref=None,
        evidence_refs=("command://pytest",),
        started_at=NOW,
        completed_at=NOW,
    )
    report = OperatorDisciplineReport(
        project_id="project-a",
        work_id="work-1",
        run_id="run-1",
        unsupported_claims=(),
        scope_violations=(),
        permission_violations=(),
        risk_mismatches=(),
        unnecessary_changes=(),
        output_tokens=None,
        changed_files=10,
        diff_lines=500,
        verdict="pass",
    )
    precondition = ControlPrecondition(
        id="control-1",
        project_id="project-a",
        kind=ControlPreconditionKind.WORKSPACE,
        description="Base state has not moved",
        check_ref="check://base_sha_matches",
        required_for=("implementing",),
    )

    assert intent.reversibility is Reversibility.SNAPSHOT_REVERSIBLE
    assert result.status == "succeeded"
    assert report.verdict == "pass"
    assert precondition.kind is ControlPreconditionKind.WORKSPACE
    for model in (WorkItem, WorkContract, Action, ActionIntent, ActionResult):
        assert {"repository", "base_sha", "result_sha", "commit_sha"}.isdisjoint(model.model_fields)


def test_work_record_is_a_mutable_projection() -> None:
    record = WorkRecord(
        work_id="work-1",
        project_id="project-a",
        source_ref=None,
        profile="software",
        status="received",
        contract_version=None,
        active_run_id=None,
        pending_gate=None,
        profile_context={},
        created_at=NOW,
        updated_at=NOW,
    )

    record.status = "contract_ready"

    assert record.status == "contract_ready"


def test_pr1_does_not_define_evidence_persistence() -> None:
    assert not hasattr(work, "Evidence")
    assert not hasattr(work, "KnowledgeItem")
