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
from datetime import datetime, timedelta, timezone
from importlib import import_module
from types import SimpleNamespace

import httpx
import pytest
from click import ClickException
from click.testing import CliRunner

from sagewai.cli import cli
from sagewai.cli.work import work as work_cli
from sagewai.fleet.execution import WorkerProcessResult
from sagewai.work import WorkEvent, WorkEventType, WorkMetrics, WorkRecord, WorkStore
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.test_cloudflare_adapter import (
    NEW_VERSION_ID,
    OLD_VERSION_ID,
    CloudflareState,
    FakeCommandRunner,
)

work_module = import_module("sagewai.cli.work")


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


def test_work_intake_runs_one_labeled_issue_scan(monkeypatch) -> None:
    seen = []

    async def fake_intake(label: str):
        seen.append(label)
        return SimpleNamespace(work_id="work-1", status="READY_TO_MERGE")

    monkeypatch.setattr(work_module, "_intake_work", fake_intake)

    result = CliRunner().invoke(work_cli, ["intake", "--label", "sagewai"])

    assert result.exit_code == 0, result.output
    assert seen == ["sagewai"]
    assert "work-1" in result.output
    assert "READY_TO_MERGE" in result.output


def test_work_intake_reports_no_unseen_labeled_issue(monkeypatch) -> None:
    async def fake_intake(_label: str):
        return None

    monkeypatch.setattr(work_module, "_intake_work", fake_intake)

    result = CliRunner().invoke(work_cli, ["intake", "--label", "sagewai"])

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

    monkeypatch.setattr(work_module, "resolve_project_id", lambda: "project-a")
    monkeypatch.setattr(work_module, "_repository_state", fake_repository_state)
    monkeypatch.setattr(
        work_module,
        "_repository_github_target",
        fake_repository_github_target,
    )
    monkeypatch.setattr(work_module, "_build_github_lifecycle", fake_build_github_lifecycle)

    record = await work_module._intake_work("sagewai")

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

    async def fake_start(description: str):
        seen.append(description)
        return SimpleNamespace(work_id="work-1", status="READY_TO_MERGE")

    monkeypatch.setattr(work_module, "_start_work", fake_start)

    result = CliRunner().invoke(
        work_cli,
        ["start", "local change description"],
    )

    assert result.exit_code == 0, result.output
    assert seen == ["local change description"]
    assert "work-1" in result.output
    assert "READY_TO_MERGE" in result.output
    assert "COMPLETE" not in result.output


def test_work_start_reports_target_validation_error(monkeypatch) -> None:
    async def fake_start(_description: str):
        raise ValueError("requested base does not match GitHub default branch")

    monkeypatch.setattr(work_module, "_start_work", fake_start)

    result = CliRunner().invoke(work_cli, ["start", "https://github.com/o/r/issues/1"])

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

    monkeypatch.setattr(work_module, "resolve_project_id", lambda: "project-a")
    monkeypatch.setattr(work_module, "_repository_state", fake_repository_state)
    monkeypatch.setattr(work_module, "_build_github_lifecycle", fake_build_github_lifecycle)

    record = await work_module._start_work("https://github.com/octocat/repo/issues/7")

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


def test_work_status_is_project_scoped_and_reports_not_found(monkeypatch) -> None:
    seen = []

    async def fake_status(work_id: str):
        seen.append(work_id)
        return None

    monkeypatch.setattr(work_module, "_status_work", fake_status)

    result = CliRunner().invoke(work_cli, ["status", "missing"])

    assert result.exit_code != 0
    assert seen == ["missing"]
    assert "not found" in result.output.lower()


def test_work_resume_uses_persisted_lifecycle(monkeypatch) -> None:
    seen = []

    async def fake_resume(work_id: str):
        seen.append(work_id)
        return SimpleNamespace(work_id=work_id, status="WORK_BLOCKED")

    monkeypatch.setattr(work_module, "_resume_work", fake_resume)

    result = CliRunner().invoke(work_cli, ["resume", "work-1"])

    assert result.exit_code == 0, result.output
    assert seen == ["work-1"]
    assert "WORK_BLOCKED" in result.output


def test_work_resume_reports_delivery_configuration_error(monkeypatch) -> None:
    async def fake_resume(_work_id: str):
        raise ValueError("Cloudflare docs delivery configuration is missing: TOKEN")

    monkeypatch.setattr(work_module, "_resume_work", fake_resume)

    result = CliRunner().invoke(work_cli, ["resume", "work-1"])

    assert result.exit_code != 0
    assert "configuration is missing" in result.output
    assert "Traceback" not in result.output


def test_work_approve_advances_the_named_canonical_gate(monkeypatch) -> None:
    seen = []

    async def fake_approve(work_id: str, gate_id: str):
        seen.append((work_id, gate_id))
        return SimpleNamespace(work_id=work_id, status="READY_TO_DELIVER")

    monkeypatch.setattr(work_module, "_approve_work", fake_approve)

    result = CliRunner().invoke(
        work_cli,
        ["approve", "work-1", "merge:work-1:42"],
    )

    assert result.exit_code == 0, result.output
    assert seen == [("work-1", "merge:work-1:42")]
    assert "READY_TO_DELIVER" in result.output


def test_work_pending_lists_canonical_attention(monkeypatch) -> None:
    async def fake_pending():
        return (
            SimpleNamespace(
                kind=SimpleNamespace(value="GATE_REQUESTED"),
                work_id="work-1",
                attention_id="merge:work-1:42",
                summary="Approve merge of PR #42.",
            ),
        )

    monkeypatch.setattr(work_module, "_pending_work", fake_pending)

    result = CliRunner().invoke(work_cli, ["pending"])

    assert result.exit_code == 0, result.output
    assert "GATE_REQUESTED" in result.output
    assert "work-1" in result.output
    assert "merge:work-1:42" in result.output
    assert "Approve merge of PR #42." in result.output


def test_work_metrics_prints_the_read_only_event_projection(monkeypatch) -> None:
    async def fake_metrics(*, work_id=None):
        assert work_id == "work-1"
        return WorkMetrics(
            project_id="project-a",
            work_id=work_id,
            control_degradation_rate=0.25,
            mean_time_to_control_restored_seconds=30.0,
            scope_violation_rate=0.1,
            repair_rate=0.2,
            rollback_rate=0.05,
        )

    monkeypatch.setattr(work_module, "_work_metrics", fake_metrics)

    result = CliRunner().invoke(work_cli, ["metrics", "--work-id", "work-1"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "control_degradation_rate": 0.25,
        "mean_time_to_control_restored_seconds": 30.0,
        "project_id": "project-a",
        "repair_rate": 0.2,
        "rollback_rate": 0.05,
        "scope_violation_rate": 0.1,
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

        async def metrics(self, *, project_id, work_id):
            calls.append(("metrics", project_id, work_id))
            return expected

    monkeypatch.setattr(work_module, "resolve_project_id", lambda: "project-a")
    monkeypatch.setattr(work_module.factory, "ensure_schema", fake_ensure_schema)
    monkeypatch.setattr(work_module.factory, "get_engine", lambda: "engine")
    monkeypatch.setattr(work_module, "WorkStore", FakeStore)

    result = await work_module._work_metrics(work_id="work-1")

    assert result == expected
    assert calls == [
        "schema",
        ("engine", "engine"),
        "init",
        ("metrics", "project-a", "work-1"),
    ]


def test_github_credentials_fail_before_remote_call_when_token_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(ClickException, match="GITHUB_TOKEN is required"):
        work_module._local_github_credentials()


@pytest.mark.asyncio
async def test_ready_to_deliver_resume_routes_to_docs_delivery(monkeypatch) -> None:
    record = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="READY_TO_DELIVER",
        source_ref="https://github.com/octocat/repo/issues/7",
    )
    repository = SimpleNamespace()
    seen = []

    async def fake_status(_work_id):
        return record

    async def fake_repository_state():
        return repository, "a" * 40

    async def fake_run_docs_delivery(value, *, project_id, repository):
        seen.append((value, project_id, repository))
        return SimpleNamespace(work_id="work-1", status="COMPLETE")

    monkeypatch.setattr(work_module, "resolve_project_id", lambda: "project-a")
    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(work_module, "_repository_state", fake_repository_state)
    monkeypatch.setattr(work_module, "_run_docs_delivery", fake_run_docs_delivery)

    result = await work_module._resume_work("work-1")

    assert result.status == "COMPLETE"
    assert seen == [(record, "project-a", repository)]


@pytest.mark.asyncio
async def test_complete_resume_returns_without_repository_or_remote_work(monkeypatch) -> None:
    record = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="COMPLETE",
        source_ref="https://github.com/octocat/repo/issues/7",
    )

    async def fake_status(_work_id):
        return record

    async def unexpected_repository_state():
        raise AssertionError("terminal Work must not inspect the repository")

    monkeypatch.setattr(work_module, "resolve_project_id", lambda: "project-a")
    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(work_module, "_repository_state", unexpected_repository_state)

    result = await work_module._resume_work("work-1")

    assert result is record


@pytest.mark.asyncio
async def test_triage_resume_repairs_new_merged_sha_then_redeploys(monkeypatch) -> None:
    current = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="TRIAGE",
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
    completed = SimpleNamespace(work_id="work-1", status="COMPLETE")
    repository = SimpleNamespace()
    seen = []

    async def fake_status(_work_id):
        return current

    async def fake_repository_state():
        return repository, "a" * 40

    async def fake_docs_delivery(value, *, project_id, repository):
        nonlocal current
        seen.append(
            (
                "delivery",
                value.profile_context["github"]["merged_sha"],
                project_id,
                repository,
            )
        )
        current = completed
        return completed

    class FakeGitHubLifecycle:
        async def resume(self, work_id, *, project_id):
            nonlocal current
            seen.append(("resume", work_id, project_id))
            current = repaired
            return repaired

    async def fake_build_github_lifecycle(*, project_id, repository):
        seen.append(("build", project_id, repository))
        return FakeGitHubLifecycle()

    monkeypatch.setattr(work_module, "resolve_project_id", lambda: "project-a")
    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(work_module, "_repository_state", fake_repository_state)
    monkeypatch.setattr(work_module, "_run_docs_delivery", fake_docs_delivery)
    monkeypatch.setattr(work_module, "_build_github_lifecycle", fake_build_github_lifecycle)

    repair_result = await work_module._resume_work("work-1")
    delivery_result = await work_module._resume_work("work-1")

    assert repair_result is repaired
    assert delivery_result is completed
    assert seen == [
        ("build", "project-a", repository),
        ("resume", "work-1", "project-a"),
        ("delivery", "b" * 40, "project-a", repository),
    ]


@pytest.mark.asyncio
async def test_delivery_gate_approval_routes_to_delivery_flow(monkeypatch) -> None:
    record = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="READY_TO_DELIVER",
        source_ref="https://github.com/octocat/repo/issues/7",
    )
    gate_id = "deploy_production:work-1:candidate:production:traffic:5%"
    repository = SimpleNamespace()
    seen = []

    async def fake_status(_work_id):
        return record

    async def fake_repository_state():
        return repository, "a" * 40

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

    async def fake_run_docs_delivery(
        value,
        *,
        project_id,
        repository,
        approve_gate_id,
    ):
        seen.append((value, project_id, repository, approve_gate_id))
        return SimpleNamespace(work_id="work-1", status="READY_TO_DELIVER")

    monkeypatch.setattr(work_module, "resolve_project_id", lambda: "project-a")
    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(work_module, "_repository_state", fake_repository_state)
    monkeypatch.setattr(work_module.factory, "ensure_schema", fake_ensure_schema)
    monkeypatch.setattr(work_module.factory, "get_engine", lambda: object())
    monkeypatch.setattr(work_module, "WorkStore", lambda engine: FakeStore())
    monkeypatch.setattr(work_module, "_run_docs_delivery", fake_run_docs_delivery)

    approved = await work_module._approve_work("work-1", gate_id)

    assert approved.status == "READY_TO_DELIVER"
    assert seen == [(record, "project-a", repository, gate_id)]


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

    async def fake_status(_work_id):
        return record

    async def unexpected_repository_state():
        raise AssertionError("terminal Work must not inspect the repository")

    monkeypatch.setattr(work_module, "resolve_project_id", lambda: "project-a")
    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(work_module, "_repository_state", unexpected_repository_state)

    result = await work_module._approve_work("work-1", "promote_rollout:stale")

    assert result is record


@pytest.mark.asyncio
async def test_triage_rejects_stale_delivery_approval_before_repository_work(
    monkeypatch,
) -> None:
    record = SimpleNamespace(
        work_id="work-1",
        project_id="project-a",
        status="TRIAGE",
        source_ref="https://github.com/octocat/repo/issues/7",
    )

    async def fake_status(_work_id):
        return record

    async def unexpected_repository_state():
        raise AssertionError("triage approval rejection must not inspect the repository")

    monkeypatch.setattr(work_module, "resolve_project_id", lambda: "project-a")
    monkeypatch.setattr(work_module, "_status_work", fake_status)
    monkeypatch.setattr(work_module, "_repository_state", unexpected_repository_state)

    with pytest.raises(ValueError, match="cannot approve a stale gate from TRIAGE"):
        await work_module._approve_work("work-1", "rollback:stale")


def test_docs_delivery_settings_require_explicit_freshness_and_rollout(
    monkeypatch,
) -> None:
    names = (
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "SAGEWAI_DOCS_KNOWN_GOOD_VERSION_ID",
        "SAGEWAI_DOCS_KNOWN_GOOD_COMMIT_SHA",
        "SAGEWAI_DOCS_KNOWN_GOOD_VERIFICATION_REF",
        "SAGEWAI_DOCS_KNOWN_GOOD_REVIEW_REF",
        "SAGEWAI_DOCS_ROLLOUT_JSON",
        "SAGEWAI_DOCS_POLICY_EVIDENCE_REF",
        "SAGEWAI_DOCS_ROLLBACK_OBSERVATION_SECONDS",
        "SAGEWAI_DOCS_OBSERVATION_SAMPLE_SECONDS",
        "SAGEWAI_DOCS_COMMAND_TIMEOUT_SECONDS",
        "SAGEWAI_DOCS_HTTP_TIMEOUT_SECONDS",
        "SAGEWAI_DOCS_HEARTBEAT_SECONDS",
        "SAGEWAI_DOCS_MINIMUM_CREDENTIAL_TTL_SECONDS",
        "SAGEWAI_DOCS_MAXIMUM_MONITORING_STALENESS_SECONDS",
    )
    for name in names:
        monkeypatch.setenv(name, "value")
    monkeypatch.setenv(
        "SAGEWAI_DOCS_ROLLOUT_JSON",
        '[{"exposure":"5%","observe_seconds":30},{"exposure":"100%","observe_seconds":60}]',
    )
    for name in (
        "SAGEWAI_DOCS_ROLLBACK_OBSERVATION_SECONDS",
        "SAGEWAI_DOCS_OBSERVATION_SAMPLE_SECONDS",
        "SAGEWAI_DOCS_COMMAND_TIMEOUT_SECONDS",
        "SAGEWAI_DOCS_HTTP_TIMEOUT_SECONDS",
        "SAGEWAI_DOCS_HEARTBEAT_SECONDS",
        "SAGEWAI_DOCS_MINIMUM_CREDENTIAL_TTL_SECONDS",
        "SAGEWAI_DOCS_MAXIMUM_MONITORING_STALENESS_SECONDS",
    ):
        monkeypatch.setenv(name, "30")

    settings = work_module._docs_delivery_settings()

    assert settings["maximum_monitoring_staleness_seconds"] == 30
    assert settings["http_timeout_seconds"] == 30
    assert [step.exposure.value for step in settings["policy"].rollout] == [
        "5%",
        "100%",
    ]

    monkeypatch.delenv("SAGEWAI_DOCS_MAXIMUM_MONITORING_STALENESS_SECONDS")
    with pytest.raises(ValueError, match="MAXIMUM_MONITORING_STALENESS"):
        work_module._docs_delivery_settings()


@pytest.mark.asyncio
async def test_docs_delivery_composition_reaches_complete_with_real_adapters(
    monkeypatch,
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    project_id = "project-a"
    work_id = "work-1"
    merged_sha = "a" * 40
    now = datetime.now(timezone.utc)
    repository = tmp_path / "repo"
    docs = repository / "apps" / "docs"
    output = docs / "out"
    output.mkdir(parents=True)
    (docs / "wrangler.toml").write_text(
        'name = "docs"\ncompatibility_date = "2026-04-04"\n',
        encoding="utf-8",
    )
    (output / "index.html").write_text("<h1>Sagewai docs</h1>", encoding="utf-8")

    record = WorkRecord(
        work_id=work_id,
        project_id=project_id,
        source_ref="https://github.com/sagewai/platform/issues/1",
        profile="software",
        status="READY_TO_DELIVER",
        contract_version=1,
        active_run_id="review-1",
        pending_gate=None,
        profile_context={"github": {"merged_sha": merged_sha}},
        created_at=now,
        updated_at=now,
    )
    store = WorkStore(engine=dialect_engine)
    await store.init()
    await store.save_work(record)
    for sequence, event_type in enumerate(
        (WorkEventType.VERIFICATION_RECORDED, WorkEventType.REVIEW_RECORDED),
        start=1,
    ):
        await store.append_event(
            WorkEvent(
                id=f"evidence-{sequence}",
                project_id=project_id,
                work_id=work_id,
                sequence=sequence,
                event_type=event_type,
                actor_type="test",
                actor_ref="test",
                payload_json={"passed": True},
                created_at=now,
            )
        )

    async def fake_ensure_schema() -> None:
        return None

    monkeypatch.setattr(work_module.factory, "ensure_schema", fake_ensure_schema)
    monkeypatch.setattr(work_module.factory, "get_engine", lambda: dialect_engine)
    monkeypatch.setattr(work_module.home, "data_dir", lambda: tmp_path / "data")
    values = {
        "CLOUDFLARE_API_TOKEN": "token",
        "CLOUDFLARE_ACCOUNT_ID": "account-1",
        "SAGEWAI_DOCS_KNOWN_GOOD_VERSION_ID": OLD_VERSION_ID,
        "SAGEWAI_DOCS_KNOWN_GOOD_COMMIT_SHA": "b" * 40,
        "SAGEWAI_DOCS_KNOWN_GOOD_VERIFICATION_REF": "verification://known-good",
        "SAGEWAI_DOCS_KNOWN_GOOD_REVIEW_REF": "review://known-good",
        "SAGEWAI_DOCS_ROLLOUT_JSON": json.dumps(
            [
                {"exposure": "5%", "observe_seconds": 1},
                {"exposure": "100%", "observe_seconds": 1},
            ]
        ),
        "SAGEWAI_DOCS_POLICY_EVIDENCE_REF": "policy://docs-production",
        "SAGEWAI_DOCS_ROLLBACK_OBSERVATION_SECONDS": "1",
        "SAGEWAI_DOCS_OBSERVATION_SAMPLE_SECONDS": "1",
        "SAGEWAI_DOCS_COMMAND_TIMEOUT_SECONDS": "30",
        "SAGEWAI_DOCS_HTTP_TIMEOUT_SECONDS": "2",
        "SAGEWAI_DOCS_HEARTBEAT_SECONDS": "1",
        "SAGEWAI_DOCS_MINIMUM_CREDENTIAL_TTL_SECONDS": "1",
        "SAGEWAI_DOCS_MAXIMUM_MONITORING_STALENESS_SECONDS": "60",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    state = CloudflareState()
    state.deployments.append(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "created_on": "2026-08-27T09:59:00Z",
            "strategy": "percentage",
            "versions": [{"version_id": OLD_VERSION_ID, "percentage": 100}],
            "annotations": {},
        }
    )

    def on_run(args) -> None:
        if "upload" in args:
            state.add_candidate_version(args[args.index("--tag") + 1])

    runner = FakeCommandRunner(
        (
            WorkerProcessResult(returncode=0, stdout=f"{merged_sha}\n", stderr=""),
            WorkerProcessResult(returncode=0, stdout="", stderr=""),
            WorkerProcessResult(returncode=0, stdout="build passed", stderr=""),
            WorkerProcessResult(
                returncode=0,
                stdout=f"Worker Version ID: {NEW_VERSION_ID}\n",
                stderr="",
            ),
        ),
        on_run=on_run,
    )

    def response(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/client/v4/user/tokens/verify":
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "result": {"status": "active"}},
            )
        if path == "/client/v4/zones":
            return httpx.Response(
                200,
                request=request,
                json={
                    "success": True,
                    "result": [{"id": "zone-1", "status": "active"}],
                },
            )
        if path == "/client/v4/graphql":
            return httpx.Response(
                200,
                request=request,
                json={
                    "data": {
                        "viewer": {
                            "zones": [
                                {
                                    "metrics": [
                                        {
                                            "count": 1,
                                            "dimensions": {
                                                "datetimeMinute": (
                                                    datetime.now(timezone.utc)
                                                    - timedelta(seconds=1)
                                                ).isoformat()
                                            },
                                        }
                                    ]
                                }
                            ]
                        }
                    },
                    "errors": None,
                },
            )
        if path.endswith(f"/versions/{OLD_VERSION_ID}"):
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "result": {"id": OLD_VERSION_ID}},
            )
        return state(request)

    current = record
    for _ in range(3):
        try:
            current = await work_module._run_docs_delivery(
                current,
                project_id=project_id,
                repository=repository,
                process_runner=runner,
                http_transport=httpx.MockTransport(response),
            )
        except work_module.DeliveryApprovalRequiredError:
            loaded = await store.load_work(work_id, project_id=project_id)
            assert loaded is not None
            current = loaded
        if current.status == "COMPLETE":
            break
        assert current.pending_gate is not None
        current = await work_module._run_docs_delivery(
            current,
            project_id=project_id,
            repository=repository,
            approve_gate_id=current.pending_gate,
            process_runner=runner,
            http_transport=httpx.MockTransport(response),
        )

    assert current.status == "COMPLETE"
    assert len(runner.calls) == 4
    assert [post["versions"] for post in state.posts] == [
        [
            {"version_id": OLD_VERSION_ID, "percentage": 95},
            {"version_id": NEW_VERSION_ID, "percentage": 5},
        ],
        [{"version_id": NEW_VERSION_ID, "percentage": 100}],
    ]
