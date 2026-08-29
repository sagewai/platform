# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""CLI coverage for direct local Work lifecycle commands."""

from __future__ import annotations

import json
from importlib import import_module
from types import SimpleNamespace

import pytest
from click import ClickException
from click.testing import CliRunner

from sagewai.cli import cli
from sagewai.cli.fleet import fleet_group
from sagewai.cli.work import work as work_cli
from sagewai.core.state import InMemoryStore
from sagewai.work import WorkEventType, WorkMetrics
from sagewai.work.profiles.software import SoftwareContractContext, SoftwareStageOperator
from tests.db.conftest import dialect_engine  # noqa: F401

work_module = import_module("sagewai.cli.work")
fleet_module = import_module("sagewai.cli.fleet")


@pytest.mark.asyncio
async def test_build_lifecycle_shares_one_artifact_store(
    monkeypatch,
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    async def fake_ensure_schema() -> None:
        return None

    async def fake_workflow_store():
        return InMemoryStore()

    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(
        "SAGEWAI_WORK_VERIFICATION_IMAGE",
        "example.invalid/verifier@sha256:" + "a" * 64,
    )
    monkeypatch.setattr(work_module.factory, "ensure_schema", fake_ensure_schema)
    monkeypatch.setattr(work_module.factory, "get_engine", lambda: dialect_engine)
    monkeypatch.setattr(
        work_module.factory,
        "get_workflow_store",
        fake_workflow_store,
    )

    lifecycle, _, _ = await work_module._build_lifecycle(
        project_id="project-a",
        repository=repository,
    )

    artifact_store = lifecycle._artifact_store
    assert lifecycle._capsule_compiler._artifact_store is artifact_store
    assert lifecycle._verifier._artifact_store is artifact_store
    assert lifecycle._verifier._runner._image.endswith("a" * 64)


def test_verification_image_is_required(monkeypatch) -> None:
    monkeypatch.delenv("SAGEWAI_WORK_VERIFICATION_IMAGE", raising=False)

    with pytest.raises(ValueError, match="never executes on the host"):
        work_module._verification_image()


def test_work_group_is_registered() -> None:
    result = CliRunner().invoke(cli, ["work", "--help"])

    assert result.exit_code == 0
    assert "start" in result.output
    assert "intake" in result.output
    assert "status" in result.output
    assert "resume" in result.output
    assert "approve" in result.output
    assert "pending" in result.output
    assert "metrics" in result.output
    assert "--project" in result.output
    assert "--execution" in result.output
    assert "--fleet-org" in result.output


@pytest.mark.parametrize(
    "command",
    (
        ("start", "description"),
        ("intake", "--label", "sagewai"),
        ("status", "work-1"),
        ("resume", "work-1"),
        ("approve", "work-1", "gate-1"),
        ("pending",),
        ("metrics",),
    ),
)
def test_every_work_command_rejects_omitted_project_scope(
    monkeypatch,
    command: tuple[str, ...],
) -> None:
    monkeypatch.setenv("SAGEWAI_PROJECT", "ambient-project-must-not-apply")

    result = CliRunner().invoke(cli, ["work", *command])

    assert result.exit_code == 2
    assert "Missing option '--project'" in result.output


def test_work_fleet_execution_context_is_explicit(monkeypatch) -> None:
    seen = []

    async def fake_pending(*, project_id):
        seen.append((project_id, work_module._work_execution_config()))
        return ()

    monkeypatch.setattr(work_module, "_pending_work", fake_pending)

    result = CliRunner().invoke(
        cli,
        [
            "work",
            "--project",
            "project-a",
            "--execution",
            "fleet",
            "--fleet-org",
            "org-a",
            "pending",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == [("project-a", ("fleet", "org-a"))]



def test_work_global_scope_is_explicit_none(monkeypatch) -> None:
    seen = []

    async def fake_pending(*, project_id):
        seen.append(project_id)
        return ()

    monkeypatch.setattr(work_module, "_pending_work", fake_pending)

    result = CliRunner().invoke(
        cli,
        ["work", "--project", "global", "pending"],
    )

    assert result.exit_code == 0, result.output
    assert seen == [None]
    assert result.output == "No pending Work attention.\n"


def test_work_intake_runs_one_labeled_issue_scan(monkeypatch) -> None:
    seen = []

    async def fake_intake(label: str, *, project_id: str | None):
        seen.append((label, project_id))
        return SimpleNamespace(work_id="work-1", status="READY_TO_MERGE")

    monkeypatch.setattr(work_module, "_intake_work", fake_intake)

    result = CliRunner().invoke(
        work_cli,
        ["--project", "project-a", "intake", "--label", "sagewai"],
    )

    assert result.exit_code == 0, result.output
    assert seen == [("sagewai", "project-a")]
    assert "work-1" in result.output
    assert "READY_TO_MERGE" in result.output


def test_work_intake_reports_no_unseen_labeled_issue(monkeypatch) -> None:
    async def fake_intake(_label: str, *, project_id: str | None):
        assert project_id == "project-a"
        return None

    monkeypatch.setattr(work_module, "_intake_work", fake_intake)

    result = CliRunner().invoke(
        work_cli,
        ["--project", "project-a", "intake", "--label", "sagewai"],
    )

    assert result.exit_code == 0, result.output
    assert (
        result.output
        == "No unstarted issues in the oldest 100 open issues labeled sagewai.\n"
    )


@pytest.mark.asyncio
async def test_intake_work_targets_the_local_github_repository(monkeypatch) -> None:
    repository = SimpleNamespace()
    seen = []

    async def fake_repository_state():
        return repository, "a" * 40

    async def fake_repository_github_target(value):
        assert value is repository
        return "octocat", "hello-world"

    class FakeGitHubLifecycle:
        async def intake_labeled(self, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(work_id="work-1", status="READY_TO_MERGE")

    async def fake_build_github_lifecycle(*, project_id, repository):
        seen.append({"build": (project_id, repository)})
        return FakeGitHubLifecycle()

    monkeypatch.setattr(work_module, "_repository_state", fake_repository_state)
    monkeypatch.setattr(
        work_module,
        "_repository_github_target",
        fake_repository_github_target,
    )
    monkeypatch.setattr(work_module, "_build_github_lifecycle", fake_build_github_lifecycle)

    record = await work_module._intake_work("sagewai", project_id="project-a")

    assert record is not None
    assert seen == [
        {"build": ("project-a", repository)},
        {
            "owner": "octocat",
            "repo": "hello-world",
            "label": "sagewai",
            "project_id": "project-a",
            "base_sha": "a" * 40,
        },
    ]


def test_work_start_runs_direct_lifecycle(monkeypatch) -> None:
    seen = []

    async def fake_start(description: str, *, project_id: str | None):
        seen.append((description, project_id))
        return SimpleNamespace(work_id="work-1", status="READY_TO_MERGE")

    monkeypatch.setattr(work_module, "_start_work", fake_start)

    result = CliRunner().invoke(
        work_cli,
        ["--project", "project-a", "start", "local change description"],
    )

    assert result.exit_code == 0, result.output
    assert seen == [("local change description", "project-a")]
    assert "work-1" in result.output
    assert "READY_TO_MERGE" in result.output
    assert "COMPLETE" not in result.output


def test_work_start_reports_target_validation_error(monkeypatch) -> None:
    async def fake_start(_description: str, *, project_id: str | None):
        assert project_id == "project-a"
        raise ValueError("requested base does not match GitHub default branch")

    monkeypatch.setattr(work_module, "_start_work", fake_start)

    result = CliRunner().invoke(
        work_cli,
        ["--project", "project-a", "start", "https://github.com/o/r/issues/1"],
    )

    assert result.exit_code != 0
    assert "requested base does not match GitHub default branch" in result.output
    assert "Traceback" not in result.output


@pytest.mark.asyncio
async def test_start_work_routes_github_issue_to_github_lifecycle(monkeypatch) -> None:
    seen = []
    repository = SimpleNamespace()

    async def fake_repository_state():
        return repository, "a" * 40

    class FakeGitHubLifecycle:
        async def start(
            self,
            *,
            issue_url: str,
            project_id: str,
            base_sha: str,
        ):
            seen.append(("start", issue_url, project_id, base_sha))
            return SimpleNamespace(work_id="work-1", status="READY_TO_MERGE")

    async def fake_build_github_lifecycle(*, project_id, repository):
        seen.append(("build", project_id, repository))
        return FakeGitHubLifecycle()

    monkeypatch.setattr(work_module, "_repository_state", fake_repository_state)
    monkeypatch.setattr(work_module, "_build_github_lifecycle", fake_build_github_lifecycle)

    record = await work_module._start_work(
        "https://github.com/octocat/repo/issues/7",
        project_id="project-a",
    )

    assert record.status == "READY_TO_MERGE"
    assert seen == [
        ("build", "project-a", repository),
        (
            "start",
            "https://github.com/octocat/repo/issues/7",
            "project-a",
            "a" * 40,
        ),
    ]


@pytest.mark.asyncio
async def test_start_work_records_selected_execution_route(monkeypatch) -> None:
    repository = SimpleNamespace()
    captured = []

    async def fake_repository_state():
        return repository, "a" * 40

    class FakeLifecycle:
        async def start(self, *, work_item, contract):
            captured.append((work_item, contract))
            return SimpleNamespace(work_id=work_item.id, status="READY_TO_MERGE")

    async def fake_build_lifecycle(
        *,
        project_id,
        repository,
        execution=None,
        fleet_org=None,
    ):
        assert project_id == "project-a"
        assert execution == "local"
        assert fleet_org is None
        return FakeLifecycle(), SimpleNamespace(), SimpleNamespace()

    monkeypatch.setattr(work_module, "_repository_state", fake_repository_state)
    monkeypatch.setattr(work_module, "_build_lifecycle", fake_build_lifecycle)

    await work_module._start_work("bounded change", project_id="project-a")

    context = SoftwareContractContext.model_validate(captured[0][1].profile_context)
    assert context.execution_route == "local"
    assert context.fleet_org_id is None
    projected = work_module._execution_route_from_events(
        [
            SimpleNamespace(
                event_type=WorkEventType.CONTRACT_PROPOSED,
                payload_json=captured[0][1].model_dump(mode="json"),
            )
        ]
    )
    assert projected == ("local", None)


def test_work_status_is_project_scoped_and_reports_not_found(monkeypatch) -> None:
    seen = []

    async def fake_status(work_id: str, *, project_id: str | None):
        seen.append((work_id, project_id))
        return None

    monkeypatch.setattr(work_module, "_status_work", fake_status)

    result = CliRunner().invoke(
        work_cli,
        ["--project", "project-a", "status", "missing"],
    )

    assert result.exit_code != 0
    assert seen == [("missing", "project-a")]
    assert "not found" in result.output.lower()


def test_work_resume_uses_persisted_lifecycle(monkeypatch) -> None:
    seen = []

    async def fake_resume(work_id: str, *, project_id: str | None):
        seen.append((work_id, project_id))
        return SimpleNamespace(work_id=work_id, status="WORK_BLOCKED")

    monkeypatch.setattr(work_module, "_resume_work", fake_resume)

    result = CliRunner().invoke(
        work_cli,
        ["--project", "project-a", "resume", "work-1"],
    )

    assert result.exit_code == 0, result.output
    assert seen == [("work-1", "project-a")]
    assert "WORK_BLOCKED" in result.output


def test_work_resume_reports_lifecycle_error(monkeypatch) -> None:
    async def fake_resume(_work_id: str, *, project_id: str | None):
        assert project_id == "project-a"
        raise ValueError("lifecycle configuration is missing: TOKEN")

    monkeypatch.setattr(work_module, "_resume_work", fake_resume)

    result = CliRunner().invoke(
        work_cli,
        ["--project", "project-a", "resume", "work-1"],
    )

    assert result.exit_code != 0
    assert "configuration is missing" in result.output
    assert "Traceback" not in result.output


def test_resume_rejects_execution_route_change_before_repository_work(monkeypatch) -> None:
    record = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="ANALYZING",
        source_ref=None,
    )

    async def fake_status(_work_id, *, project_id):
        assert project_id == "project-a"
        return record

    async def fake_stored_route(_work_id, *, project_id):
        assert project_id == "project-a"
        return "fleet", "org-a"

    async def unexpected_repository_state():
        raise AssertionError("route mismatch must fail before repository work")

    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(
        work_module,
        "_stored_work_execution_route",
        fake_stored_route,
        raising=False,
    )
    monkeypatch.setattr(work_module, "_repository_state", unexpected_repository_state)

    result = CliRunner().invoke(
        work_cli,
        ["--project", "project-a", "resume", "work-1"],
    )

    assert result.exit_code != 0
    assert "bound to fleet execution" in result.output
    assert "--execution fleet --fleet-org org-a" in result.output


def test_resume_accepts_the_same_fleet_route(monkeypatch) -> None:
    record = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="ANALYZING",
        source_ref=None,
    )
    repository = SimpleNamespace()
    route_reads = []

    async def fake_status(_work_id, *, project_id):
        assert project_id == "project-a"
        return record

    async def fake_stored_route(work_id, *, project_id):
        route_reads.append((work_id, project_id))
        return "fleet", "org-a"

    async def fake_repository_state():
        return repository, "a" * 40

    class FakeLifecycle:
        async def resume(self, work_id, *, project_id):
            assert (work_id, project_id) == ("work-1", "project-a")
            return SimpleNamespace(work_id=work_id, status="WORK_BLOCKED")

    async def fake_build_lifecycle(*, project_id, repository):
        assert project_id == "project-a"
        assert repository is not None
        return FakeLifecycle(), SimpleNamespace(), SimpleNamespace()

    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(
        work_module,
        "_stored_work_execution_route",
        fake_stored_route,
        raising=False,
    )
    monkeypatch.setattr(work_module, "_repository_state", fake_repository_state)
    monkeypatch.setattr(work_module, "_build_lifecycle", fake_build_lifecycle)

    result = CliRunner().invoke(
        work_cli,
        [
            "--project",
            "project-a",
            "--execution",
            "fleet",
            "--fleet-org",
            "org-a",
            "resume",
            "work-1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert route_reads == [("work-1", "project-a")]
    assert "WORK_BLOCKED" in result.output


def test_work_approve_advances_the_named_canonical_gate(monkeypatch) -> None:
    seen = []

    async def fake_approve(
        work_id: str,
        gate_id: str,
        *,
        project_id: str | None,
    ):
        seen.append((work_id, gate_id, project_id))
        return SimpleNamespace(work_id=work_id, status="READY_TO_MERGE")

    monkeypatch.setattr(work_module, "_approve_work", fake_approve)

    result = CliRunner().invoke(
        work_cli,
        [
            "--project",
            "project-a",
            "approve",
            "work-1",
            "merge:work-1:42",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == [("work-1", "merge:work-1:42", "project-a")]
    assert "READY_TO_MERGE" in result.output


def test_work_pending_lists_canonical_attention(monkeypatch) -> None:
    async def fake_pending(*, project_id: str | None):
        assert project_id == "project-a"
        return (
            SimpleNamespace(
                kind=SimpleNamespace(value="GATE_REQUESTED"),
                work_id="work-1",
                attention_id="merge:work-1:42",
                summary="Approve merge of PR #42.",
            ),
            SimpleNamespace(
                kind=SimpleNamespace(value="PRODUCTION_INCIDENT"),
                work_id="work-2",
                attention_id="rollback-refused",
                summary="CRITICAL: production incident for deployment production-1",
            ),
        )

    monkeypatch.setattr(work_module, "_pending_work", fake_pending)

    result = CliRunner().invoke(
        work_cli,
        ["--project", "project-a", "pending"],
    )

    assert result.exit_code == 0, result.output
    assert "GATE_REQUESTED" in result.output
    assert "work-1" in result.output
    assert "merge:work-1:42" in result.output
    assert "Approve merge of PR #42." in result.output
    assert "PRODUCTION_INCIDENT" in result.output
    assert "rollback-refused" in result.output
    assert "CRITICAL: production incident" in result.output


def test_work_metrics_prints_the_read_only_event_projection(monkeypatch) -> None:
    async def fake_metrics(
        *,
        project_id,
        work_id=None,
        profile=None,
        runtime=None,
    ):
        assert (project_id, work_id, profile, runtime) == (
            "project-a",
            "work-1",
            "software",
            "codex",
        )
        return WorkMetrics(
            project_id="project-a",
            work_id=work_id,
            profile=profile,
            runtime=runtime,
            control_degradation_rate=0.25,
            mean_time_to_control_restored_seconds=30.0,
            scope_violation_rate=0.1,
            repair_rate=0.2,
            rollback_rate=0.05,
        )

    monkeypatch.setattr(work_module, "_work_metrics", fake_metrics)

    result = CliRunner().invoke(
        work_cli,
        [
            "--project",
            "project-a",
            "metrics",
            "--work-id",
            "work-1",
            "--profile",
            "software",
            "--runtime",
            "codex",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "artifact_bytes_referenced": None,
        "control_degradation_rate": 0.25,
        "false_positive_blocked_rate": None,
        "knowledge_items_considered": None,
        "knowledge_items_selected": None,
        "mean_blind_window_seconds": None,
        "mean_changed_files_per_accepted_work_item": None,
        "mean_diff_lines_per_accepted_change": None,
        "mean_time_to_control_restored_seconds": 30.0,
        "missing_context_repair_rate": None,
        "permission_escalation_accuracy": None,
        "profile": "software",
        "project_id": "project-a",
        "repair_rate": 0.2,
        "retrieval_hit_rate": None,
        "risk_classification_accuracy": None,
        "rollback_rate": 0.05,
        "runtime": "codex",
        "scope_violation_rate": 0.1,
        "task_capsule_tokens": None,
        "unsupported_claim_rate": None,
        "verbosity_output_token_ratio": None,
        "work_id": "work-1",
    }


@pytest.mark.asyncio
async def test_work_metrics_queries_the_resolved_project_store(monkeypatch) -> None:
    expected = WorkMetrics(
        project_id="project-a",
        work_id="work-1",
        control_degradation_rate=0.0,
        mean_time_to_control_restored_seconds=None,
        scope_violation_rate=0.0,
        repair_rate=0.0,
        rollback_rate=0.0,
    )
    calls = []

    async def fake_ensure_schema():
        calls.append("schema")

    class FakeStore:
        def __init__(self, *, engine):
            calls.append(("engine", engine))

        async def init(self):
            calls.append("init")

        async def metrics(self, *, project_id, work_id, profile, runtime):
            calls.append(("metrics", project_id, work_id, profile, runtime))
            return expected

    monkeypatch.setattr(work_module.factory, "ensure_schema", fake_ensure_schema)
    monkeypatch.setattr(work_module.factory, "get_engine", lambda: "engine")
    monkeypatch.setattr(work_module, "WorkStore", FakeStore)

    result = await work_module._work_metrics(
        project_id="project-a",
        work_id="work-1",
        profile="software",
        runtime="codex",
    )

    assert result == expected
    assert calls == [
        "schema",
        ("engine", "engine"),
        "init",
        ("metrics", "project-a", "work-1", "software", "codex"),
    ]


def test_github_credentials_fail_before_remote_call_when_token_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(ClickException, match="GITHUB_TOKEN is required"):
        work_module._local_github_credentials()


@pytest.mark.parametrize(
    "delivery_status",
    (
        "READY_TO_DELIVER",
        "RELEASING",
        "STAGING",
        "PRODUCTION_CANARY",
        "PRODUCTION_ROLLOUT",
        "SOAKING",
        "ROLLING_BACK",
    ),
)
@pytest.mark.asyncio
async def test_delivery_phase_resume_does_not_infer_provider(
    monkeypatch,
    delivery_status: str,
) -> None:
    record = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status=delivery_status,
        source_ref="https://github.com/octocat/repo/issues/7",
    )

    async def fake_status(_work_id, *, project_id):
        assert project_id == "project-a"
        return record

    async def unexpected_repository_state():
        raise AssertionError("generic resume must not select a delivery provider")

    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(
        work_module, "_repository_state", unexpected_repository_state
    )

    result = await work_module._resume_work("work-1", project_id="project-a")

    assert result is record



@pytest.mark.asyncio
async def test_complete_resume_returns_without_repository_or_remote_work(monkeypatch) -> None:
    record = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="COMPLETE",
        source_ref="https://github.com/octocat/repo/issues/7",
    )

    async def fake_status(_work_id, *, project_id):
        assert project_id == "project-a"
        return record

    async def unexpected_repository_state():
        raise AssertionError("terminal Work must not inspect the repository")

    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(work_module, "_repository_state", unexpected_repository_state)

    result = await work_module._resume_work("work-1", project_id="project-a")

    assert result is record


@pytest.mark.asyncio
async def test_triaging_resume_repairs_without_inferring_delivery(monkeypatch) -> None:
    current = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="TRIAGING",
        source_ref="https://github.com/octocat/repo/issues/7",
        profile_context={"github": {"merged_sha": "a" * 40}},
    )
    repaired = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="READY_TO_DELIVER",
        source_ref="https://github.com/octocat/repo/issues/7",
        profile_context={"github": {"merged_sha": "b" * 40}},
    )
    repository = SimpleNamespace()
    seen = []

    async def fake_status(_work_id, *, project_id):
        assert project_id == "project-a"
        return current

    async def fake_stored_route(_work_id, *, project_id):
        assert project_id == "project-a"
        return "local", None

    async def fake_repository_state():
        seen.append("repository")
        return repository, "a" * 40

    class FakeGitHubLifecycle:
        async def resume(self, work_id, *, project_id):
            nonlocal current
            seen.append(("resume", work_id, project_id))
            current = repaired
            return repaired

    async def fake_build_github_lifecycle(*, project_id, repository):
        seen.append(("build", project_id, repository))
        return FakeGitHubLifecycle()

    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(
        work_module,
        "_stored_work_execution_route",
        fake_stored_route,
    )
    monkeypatch.setattr(work_module, "_repository_state", fake_repository_state)
    monkeypatch.setattr(
        work_module, "_build_github_lifecycle", fake_build_github_lifecycle
    )

    repair_result = await work_module._resume_work(
        "work-1",
        project_id="project-a",
    )
    frozen_result = await work_module._resume_work(
        "work-1",
        project_id="project-a",
    )

    assert repair_result is frozen_result is repaired
    assert seen == [
        "repository",
        ("build", "project-a", repository),
        ("resume", "work-1", "project-a"),
    ]


@pytest.mark.asyncio
async def test_delivery_gate_approval_requires_explicit_adapter(monkeypatch) -> None:
    record = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="READY_TO_DELIVER",
        source_ref="https://github.com/octocat/repo/issues/7",
    )
    gate_id = "deploy_production:work-1:candidate:production:traffic:5%"

    async def fake_status(_work_id, *, project_id):
        assert project_id == "project-a"
        return record

    async def unexpected_repository_state():
        raise AssertionError("delivery approval must not infer a provider")

    async def fake_ensure_schema():
        return None

    class FakeStore:
        async def init(self):
            return None

        async def read_events(self, work_id, *, project_id):
            return [
                SimpleNamespace(
                    event_type=WorkEventType.GATE_REQUESTED,
                    payload_json={
                        "gate_id": gate_id,
                        "action": {"action": "deploy_production"},
                    },
                )
            ]

    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(
        work_module, "_repository_state", unexpected_repository_state
    )
    monkeypatch.setattr(work_module.factory, "ensure_schema", fake_ensure_schema)
    monkeypatch.setattr(work_module.factory, "get_engine", lambda: object())
    monkeypatch.setattr(work_module, "WorkStore", lambda engine: FakeStore())

    with pytest.raises(
        ValueError,
        match="delivery approval requires an explicitly selected adapter",
    ):
        await work_module._approve_work(
            "work-1",
            gate_id,
            project_id="project-a",
        )


@pytest.mark.asyncio
async def test_complete_delivery_approval_is_a_noop_before_repository_work(
    monkeypatch,
) -> None:
    record = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="COMPLETE",
        source_ref="https://github.com/octocat/repo/issues/7",
    )

    async def fake_status(_work_id, *, project_id):
        assert project_id == "project-a"
        return record

    async def unexpected_repository_state():
        raise AssertionError("terminal Work must not inspect the repository")

    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(work_module, "_repository_state", unexpected_repository_state)

    result = await work_module._approve_work(
        "work-1",
        "promote_rollout:stale",
        project_id="project-a",
    )

    assert result is record


@pytest.mark.asyncio
async def test_triaging_rejects_stale_delivery_approval_before_repository_work(
    monkeypatch,
) -> None:
    record = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="TRIAGING",
        source_ref="https://github.com/octocat/repo/issues/7",
    )

    async def fake_status(_work_id, *, project_id):
        assert project_id == "project-a"
        return record

    async def unexpected_repository_state():
        raise AssertionError("triaging approval rejection must not inspect the repository")

    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(work_module, "_repository_state", unexpected_repository_state)

    with pytest.raises(ValueError, match="cannot approve a stale gate from TRIAGING"):
        await work_module._approve_work(
            "work-1",
            "rollback:stale",
            project_id="project-a",
        )


def test_work_fleet_execution_requires_explicit_org() -> None:
    result = CliRunner().invoke(
        work_cli,
        ["--project", "project-a", "--execution", "fleet", "pending"],
    )

    assert result.exit_code == 2
    assert "--fleet-org is required with --execution fleet" in result.output


@pytest.mark.asyncio
async def test_build_lifecycle_composes_persistent_fleet_stages(
    monkeypatch,
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    initialized = []
    fleet_calls = []

    class FakeRegistry:
        def __init__(self, *, engine):
            assert engine is dialect_engine

        async def init(self):
            initialized.append("registry")

    class FakeTaskStore:
        def __init__(self, *, engine):
            assert engine is dialect_engine

        async def init(self):
            initialized.append("tasks")

    class FakeTransport:
        def __init__(self, *, repository_ref):
            assert repository_ref == "github://sagewai/platform"

    async def fake_ensure_schema() -> None:
        return None

    async def fake_workflow_store():
        return InMemoryStore()

    async def fake_repository_ref(_repository):
        return "github://sagewai/platform"

    def fake_fleet(cls, **kwargs):
        fleet_calls.append(kwargs)
        return cls(
            actor_ref=kwargs["actor_ref"],
            runtime=SimpleNamespace(name=f"fleet:{kwargs['runtime_capability']}"),
            capabilities=kwargs["capabilities"],
            controller=kwargs["controller"],
        )

    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(
        "SAGEWAI_WORK_VERIFICATION_IMAGE",
        "example.invalid/verifier@sha256:" + "a" * 64,
    )
    monkeypatch.setattr(work_module.factory, "ensure_schema", fake_ensure_schema)
    monkeypatch.setattr(work_module.factory, "get_engine", lambda: dialect_engine)
    monkeypatch.setattr(work_module.factory, "get_workflow_store", fake_workflow_store)
    monkeypatch.setattr(work_module, "PostgresFleetRegistry", FakeRegistry)
    monkeypatch.setattr(work_module, "PostgresTaskStore", FakeTaskStore)
    monkeypatch.setattr(work_module, "SoftwareFleetWorkspaceTransport", FakeTransport)
    monkeypatch.setattr(work_module, "_software_repository_ref", fake_repository_ref)
    monkeypatch.setattr(SoftwareStageOperator, "fleet", classmethod(fake_fleet))

    lifecycle, _, _ = await work_module._build_lifecycle(
        project_id="project-a",
        repository=repository,
        execution="fleet",
        fleet_org="org-a",
    )

    assert initialized == ["registry", "tasks"]
    assert [call["runtime_capability"] for call in fleet_calls] == [
        "runtime.claude",
        "runtime.codex",
        "runtime.claude",
        "runtime.codex",
    ]
    assert all(call["org_id"] == "org-a" for call in fleet_calls)
    assert all(
        call["workspace_transport"].__class__ is FakeTransport
        for call in fleet_calls
    )
    assert lifecycle._analyst.runtime.name == "fleet:runtime.claude"
    assert lifecycle._implementer.runtime.name == "fleet:runtime.codex"
    assert lifecycle._reviewer.runtime.name == "fleet:runtime.claude"
    assert lifecycle._repairer.runtime.name == "fleet:runtime.codex"


def test_fleet_native_runtime_requires_explicit_project_and_repository(tmp_path) -> None:
    missing_project = CliRunner().invoke(
        fleet_group,
        [
            "run",
            "--name",
            "worker",
            "--capabilities",
            "runtime.claude",
            "--register-only",
        ],
    )
    assert missing_project.exit_code == 2
    assert "--project is required" in missing_project.output

    missing_repository = CliRunner().invoke(
        fleet_group,
        [
            "run",
            "--name",
            "worker",
            "--capabilities",
            "runtime.claude",
            "--project",
            "project-a",
            "--register-only",
        ],
    )
    assert missing_repository.exit_code == 2
    assert "--work-repository is required" in missing_repository.output


def test_fleet_native_runtime_builds_typed_local_handler(monkeypatch, tmp_path) -> None:
    seen = {}

    class FakeResolver:
        def __init__(self, *, repository):
            seen["repository"] = repository

    class FakeHandler:
        def __init__(self, *, workspace_resolver):
            seen["resolver"] = workspace_resolver

    class FakeRunner:
        def __init__(self, **kwargs):
            seen["runner"] = kwargs

        async def register(self):
            return "worker-1", "approved"

        async def aclose(self):
            return None

    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(fleet_module, "SoftwareFleetWorkspaceResolver", FakeResolver)
    monkeypatch.setattr(fleet_module, "SoftwareFleetTaskHandler", FakeHandler)
    monkeypatch.setattr(fleet_module, "WorkerRunner", FakeRunner)

    result = CliRunner().invoke(
        fleet_group,
        [
            "run",
            "--name",
            "worker",
            "--capabilities",
            "runtime.codex,runtime.claude",
            "--project",
            "project-a",
            "--work-repository",
            str(repository),
            "--register-only",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["repository"] == repository.resolve()
    assert seen["runner"]["project"] == "project-a"
    assert seen["runner"]["task_handler"].__class__ is FakeHandler
    assert "token" not in seen["runner"]["task_handler"].__dict__
