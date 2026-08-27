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

from importlib import import_module
from types import SimpleNamespace

import pytest
from click import ClickException
from click.testing import CliRunner

from sagewai.cli import cli
from sagewai.cli.work import work as work_cli
from sagewai.work import WorkEventType

work_module = import_module("sagewai.cli.work")


def test_work_group_is_registered() -> None:
    result = CliRunner().invoke(cli, ["work", "--help"])

    assert result.exit_code == 0
    assert "start" in result.output
    assert "status" in result.output
    assert "resume" in result.output
    assert "approve" in result.output
    assert "pending" in result.output


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
        "SAGEWAI_DOCS_HEARTBEAT_SECONDS",
        "SAGEWAI_DOCS_MINIMUM_CREDENTIAL_TTL_SECONDS",
        "SAGEWAI_DOCS_MAXIMUM_MONITORING_STALENESS_SECONDS",
    ):
        monkeypatch.setenv(name, "30")

    settings = work_module._docs_delivery_settings()

    assert settings["maximum_monitoring_staleness_seconds"] == 30
    assert [step.exposure.value for step in settings["policy"].rollout] == [
        "5%",
        "100%",
    ]

    monkeypatch.delenv("SAGEWAI_DOCS_MAXIMUM_MONITORING_STALENESS_SECONDS")
    with pytest.raises(ValueError, match="MAXIMUM_MONITORING_STALENESS"):
        work_module._docs_delivery_settings()
