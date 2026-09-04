# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The report Work lifecycle composes, verifies, reviews, and gates delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

import sagewai.work.profiles.report.assembly as assembly
from sagewai.artifacts.models import ArtifactRef
from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.harness.discovery import DiscoveredServer
from sagewai.work.capsule import TaskCapsuleCompiler
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.knowledge.store import KnowledgeStore
from sagewai.work.models import (
    ProposedAcceptanceCriterion,
    ReviewResult,
)
from sagewai.work.profiles.report.assembly import build_report_stack
from sagewai.work.profiles.report.lifecycle import ReportLifecycle, ReportOperator
from sagewai.work.profiles.report.models import ReportArchive
from sagewai.work.profiles.report.profile import ReportProfile
from sagewai.work.profiles.report.sinks import ConsoleSink, GitHubIssueSink
from sagewai.work.runtime import (
    CapabilityGrant,
    CapabilitySet,
    OperatorResult,
    OperatorRuntime,
    WorkRequest,
)
from sagewai.work.store import WorkStore
from sagewai.work.tasks.models import (
    Authority,
    ExecutionRoute,
    HarnessTier,
    ReportTarget,
    RoutingPolicy,
    Sink,
    Task,
    TaskKind,
    TaskOrigin,
)
from sagewai.work.tasks.plan import PlanStep
from sagewai.work.tasks.scratch import ScratchWorkspace, ScratchWorkspaceManager
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_software_kernel import RecordingGitHub

BODY = "# Summary\n\nVendor A shipped a queue.\n"
WORK_ID = "t1:report:1:s1"
PROJECT = "project-a"
TASK_ID = "t1"
NOW = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
ISSUE_URL = "https://github.com/octocat/hello-world/issues/42"


@dataclass(frozen=True)
class _Runtime:
    name: str


class _FakeController:
    def __init__(
        self,
        *,
        work_store: WorkStore,
        body: str = BODY,
        review_verdict: str = "accept",
        summary: str | None = None,
        status: str = "passed",
        review_finding: str | None = None,
    ) -> None:
        self._work_store = work_store
        self._body = body
        self._review_verdict = review_verdict
        self._summary = summary
        self._status = status
        self._review_finding = review_finding
        self.findings: list[list[str]] = []

    async def run(
        self,
        *,
        runtime: OperatorRuntime,
        request: WorkRequest,
        capsule,
        capabilities: CapabilitySet,
        workspace: ScratchWorkspace,
    ) -> OperatorResult:
        events = await self._work_store.read_events(request.work_id, project_id=request.project_id)
        await self._work_store.append_event(
            WorkEvent(
                id=f"{request.run_id}:fake-start:{len(events) + 1}",
                project_id=request.project_id,
                work_id=request.work_id,
                sequence=len(events) + 1,
                event_type=WorkEventType.STAGE_STARTED,
                actor_type="system",
                actor_ref=f"runtime:{runtime.name}",
                payload_json={"run_id": request.run_id, "stage": request.stage},
                created_at=NOW,
            )
        )
        if request.stage == "compose":
            self.findings.append(list(capsule.profile_context["findings"]))
            (workspace.path / "sources").mkdir(parents=True, exist_ok=True)
            (workspace.path / "report.md").write_text(self._body, encoding="utf-8")
            (workspace.path / "sources" / "a.txt").write_text("snapshot\n", encoding="utf-8")
            profile_context = {
                "report_result": {
                    "attempt_id": request.run_id,
                    "report_path": "report.md",
                    "sources_used": (
                        {
                            "url": "https://a.example/blog",
                            "path": "sources/a.txt",
                            "fetched_at": NOW.isoformat(),
                        },
                    ),
                    "claims": (
                        {
                            "statement": "Vendor A shipped a queue.",
                            "source_urls": ("https://a.example/blog",),
                        },
                    ),
                }
            }
        else:
            profile_context = {
                "review_result": _review_result(
                    request.run_id,
                    self._review_verdict,
                    finding=self._review_finding,
                ).model_dump(mode="json")
            }
        return OperatorResult(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            status=self._status,
            summary=self._summary or f"{request.stage} {self._status}",
            evidence_refs=(),
            artifact_refs=(),
            changes=(),
            verification=(),
            risks=(),
            action_results=(),
            output_tokens=1,
            profile_context=profile_context,
        )


def _review_result(attempt_id: str, verdict: str, *, finding: str | None = None) -> ReviewResult:
    if finding is None:
        unsupported = ("Summary needs stronger support.",) if verdict == "repair" else ()
    else:
        unsupported = (finding,)
    return ReviewResult(
        project_id=PROJECT,
        attempt_id=attempt_id,
        verdict=verdict,
        findings=(),
        evidence_refs=(),
        introduced_assumptions=(),
        unsupported_claims=unsupported,
        scope_expansions=(),
        unsupported_implementation_choices=(),
    )


def _brief() -> ArtifactRef:
    return ArtifactRef(
        project_id=PROJECT,
        digest="sha256:" + "a" * 64,
        media_type="text/markdown",
        size_bytes=12,
        storage_ref="artifact://sha256:" + "a" * 64,
        created_at=NOW,
        created_by="test",
    )


def _source_grant() -> CapabilityGrant:
    return CapabilityGrant(
        project_id=PROJECT,
        name="browser:a",
        kind="browser",
        scope={"allowed_hosts": ("a.example",)},
        permissions=("read",),
    )


class _FakeSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values
        self.calls: list[list[str]] = []

    async def env_for(self, *, project_id, run_id, agent_id, declared_scopes, **_kwargs):
        self.calls.append(list(declared_scopes))
        return dict(self._values)


def _report_task(*, issue_sink: bool = False) -> Task:
    sinks = (
        (
            Sink(
                kind="github_issue",
                version=2,
                issue_url=ISSUE_URL,
            ),
        )
        if issue_sink
        else ()
    )
    return Task(
        id=TASK_ID,
        project_id=PROJECT,
        kind=TaskKind.BATCH,
        origin=TaskOrigin.HUMAN,
        title="Research Vendor A",
        brief_ref=_brief(),
        brief_summary="Research Vendor A",
        template_id="scheduled_research_report",
        template_version="2",
        profile="report",
        target=ReportTarget(
            sources=(_source_grant(),),
            sinks=sinks,
            required_sections=("Summary",),
        ),
        authority=Authority.for_kind(TaskKind.BATCH),
        routing=RoutingPolicy(),
        execution=ExecutionRoute(route="local"),
        created_by="test",
        created_at=NOW,
    )


def _step() -> PlanStep:
    return PlanStep(
        id="s1",
        title="Research report",
        goal="Write a sourced report",
        allowed_scope=(".",),
        acceptance_criteria=(
            ProposedAcceptanceCriterion(
                statement="The report is sourced.",
                verification_kind="profile",
            ),
        ),
        risk="low",
        domain="report",
        size="s",
    )


def _start_kwargs(*, issue_sink: bool = False) -> dict:
    return {
        "work_id": WORK_ID,
        "project_id": PROJECT,
        "task": _report_task(issue_sink=issue_sink),
        "cycle": 1,
        "step": _step(),
        "source_ref": f"report://{TASK_ID}/1/report",
    }


async def _lifecycle(
    engine,
    tmp_path,
    *,
    body: str = BODY,
    review_verdict: str = "accept",
    credential_values: dict[str, str] | None = None,
    issue_sink: bool = False,
    compose_status: str = "passed",
    compose_summary: str | None = None,
    review_finding: str | None = None,
) -> tuple[ReportLifecycle, WorkStore, LocalArtifactStore, RecordingGitHub]:
    work_store = WorkStore(engine=engine)
    await work_store.init()
    knowledge_store = KnowledgeStore(engine=engine)
    await knowledge_store.init()
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    scratch = ScratchWorkspaceManager(root=tmp_path / "scratch")
    compiler = TaskCapsuleCompiler(knowledge_store=knowledge_store, artifact_store=artifacts)
    capabilities = CapabilitySet(project_id=PROJECT, grants=(_source_grant(),))
    composer = ReportOperator(
        actor_ref="runtime:fake:composer",
        runtime=_Runtime("fake:composer"),
        controller=_FakeController(
            work_store=work_store,
            body=body,
            status=compose_status,
            summary=compose_summary,
        ),
        capabilities=capabilities,
    )
    reviewer = ReportOperator(
        actor_ref="runtime:fake:reviewer",
        runtime=_Runtime("fake:reviewer"),
        controller=_FakeController(
            work_store=work_store,
            review_verdict=review_verdict,
            review_finding=review_finding,
        ),
        capabilities=capabilities,
    )
    github = RecordingGitHub()
    sinks = {"console": ConsoleSink(artifact_store=artifacts)}
    if issue_sink:
        sinks["github_issue"] = GitHubIssueSink(github=github)
    lifecycle = ReportLifecycle(
        profile=ReportProfile(),
        work_store=work_store,
        capsule_compiler=compiler,
        scratch_manager=scratch,
        artifact_store=artifacts,
        composer=(composer,),
        reviewer=(reviewer,),
        sinks=sinks,
        credential_values=credential_values,
    )
    return lifecycle, work_store, artifacts, github


@pytest.mark.asyncio
async def test_the_assembled_stack_drives_a_report_to_console_delivery(
    dialect_engine,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))

    async def _unexpected_discovery():
        raise AssertionError("no medium tier should skip backend discovery")

    monkeypatch.setattr(assembly, "discover_local_backends", _unexpected_discovery)
    stack = await build_report_stack(
        project_id=PROJECT,
        target=_report_task().target,
        harness_tiers={},
        engine=dialect_engine,
        controller_factory=lambda **kwargs: _FakeController(work_store=kwargs["work_store"]),
    )

    assert isinstance(stack.scratch_manager, ScratchWorkspaceManager)
    assert [operator.actor_ref for operator in stack.lifecycle._composer] == [
        "runtime:claude:analysis"
    ]
    record = await stack.lifecycle.start(**_start_kwargs())
    assert record.status == "COMPOSING"

    record = await stack.lifecycle.resume(WORK_ID, project_id=PROJECT)
    assert record.status == "REVIEWING"

    record = await stack.lifecycle.resume(WORK_ID, project_id=PROJECT)
    assert record.status == "READY_TO_DELIVER"

    record, receipts = await stack.lifecycle.deliver(WORK_ID, project_id=PROJECT, sink_version=1)
    assert record.status == "COMPLETE"
    assert receipts[0].result.status == "succeeded"
    assert [
        event.payload_json["sink"]
        for event in await stack.work_store.read_events(WORK_ID, project_id=PROJECT)
        if event.event_type is WorkEventType.EXECUTION_RECORDED
    ] == ["console"]
    assert stack.lifecycle._reviewer[0].capabilities == stack.read_capabilities


@pytest.mark.asyncio
async def test_the_assembled_stack_uses_harness_medium_before_claude(
    dialect_engine,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    calls = 0

    async def _discover_local_backends():
        nonlocal calls
        calls += 1
        return {
            "ollama": DiscoveredServer(
                name="ollama",
                base_url="http://localhost:11434",
                openai_compat_url="http://localhost:11434",
                models=["llama3"],
            )
        }

    monkeypatch.setattr(assembly, "discover_local_backends", _discover_local_backends)
    stack = await build_report_stack(
        project_id=PROJECT,
        target=_report_task().target,
        harness_tiers={"medium": HarnessTier(backend="ollama", model="llama3")},
        engine=dialect_engine,
        controller_factory=lambda **kwargs: _FakeController(work_store=kwargs["work_store"]),
    )

    assert calls == 1
    assert [operator.actor_ref for operator in stack.lifecycle._composer] == [
        "runtime:harness:medium",
        "runtime:claude:analysis",
    ]


@pytest.mark.asyncio
async def test_the_report_stack_resolves_source_grant_credentials_for_harness(
    dialect_engine,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))

    async def _discover_local_backends():
        return {
            "ollama": DiscoveredServer(
                name="ollama",
                base_url="http://localhost:11434",
                openai_compat_url="http://localhost:11434",
                models=["llama3"],
            )
        }

    source = _source_grant().model_copy(update={"credential_ref": "GITHUB_TOKEN"})
    secrets = _FakeSecrets({"GITHUB_TOKEN": "ghp_x"})
    monkeypatch.setattr(assembly, "discover_local_backends", _discover_local_backends)
    stack = await build_report_stack(
        project_id=PROJECT,
        target=_report_task().target.model_copy(update={"sources": (source,)}),
        harness_tiers={"medium": HarnessTier(backend="ollama", model="llama3")},
        engine=dialect_engine,
        controller_factory=lambda **kwargs: _FakeController(work_store=kwargs["work_store"]),
        secret_provider=secrets,
    )

    harness = stack.lifecycle._composer[0].runtime
    assert secrets.calls == [["GITHUB_TOKEN"]]
    assert harness._credential_values == {"GITHUB_TOKEN": "ghp_x"}
    assert stack.lifecycle._credential_values == {"GITHUB_TOKEN": "ghp_x"}


@pytest.mark.asyncio
async def test_github_issue_sink_needs_a_github_client(
    dialect_engine,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))

    with pytest.raises(ValueError, match="a github_issue sink needs a GitHub client"):
        await build_report_stack(
            project_id=PROJECT,
            target=_report_task(issue_sink=True).target,
            harness_tiers={},
            engine=dialect_engine,
            controller_factory=lambda **kwargs: _FakeController(work_store=kwargs["work_store"]),
        )


@pytest.mark.asyncio
async def test_a_report_composes_verifies_reviews_and_delivers(
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    lifecycle, store, artifacts, _github = await _lifecycle(dialect_engine, tmp_path)

    record = await lifecycle.start(**_start_kwargs())
    assert record.status == "COMPOSING"

    record = await lifecycle.resume(WORK_ID, project_id=PROJECT)
    assert record.status == "REVIEWING"
    archive = ReportArchive.model_validate(record.profile_context["report"]["archive"])
    assert len(archive.snapshots) == 1
    assert archive.claims[0].snapshot_refs == (archive.snapshots[0].snapshot_ref,)
    assert artifacts.read(archive.report_ref, project_id=PROJECT).startswith(b"# Summary")

    record = await lifecycle.resume(WORK_ID, project_id=PROJECT)
    assert record.status == "READY_TO_DELIVER"
    assert record.profile_context["report"]["pending_sink_version"] == 1

    record, receipts = await lifecycle.deliver(WORK_ID, project_id=PROJECT, sink_version=1)
    assert record.status == "COMPLETE"
    assert receipts[0].result.status == "succeeded"
    assert receipts[0].observation["check"] == "artifact_read_back"
    events = await store.read_events(WORK_ID, project_id=PROJECT)
    assert [
        event.payload_json["sink"]
        for event in events
        if event.event_type is WorkEventType.EXECUTION_RECORDED
    ] == ["console"]
    assert {
        (event.payload_json["stage"], event.actor_ref)
        for event in events
        if event.event_type is WorkEventType.STAGE_STARTED
    } == {
        ("compose", "runtime:fake:composer"),
        ("review", "runtime:fake:reviewer"),
    }
    assert record.profile_context["task_id"] == TASK_ID


@pytest.mark.asyncio
async def test_a_failed_deterministic_check_repairs_twice_then_blocks(
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    lifecycle, store, _artifacts, _github = await _lifecycle(
        dialect_engine, tmp_path, body="No headings here.\n"
    )
    await lifecycle.start(**_start_kwargs())
    for _ in range(3):
        record = await lifecycle.resume(WORK_ID, project_id=PROJECT)
    assert record.status == "WORK_BLOCKED"
    failures = [
        event.payload_json["failures"]
        for event in await store.read_events(WORK_ID, project_id=PROJECT)
        if event.event_type is WorkEventType.VERIFICATION_RECORDED
    ]
    assert len(failures) == 3 and all("Summary" in "".join(f) for f in failures)
    composer = lifecycle._composer[0].controller
    assert composer.findings[1] == ["required section 'Summary' is missing"]


@pytest.mark.asyncio
async def test_a_reviewer_repair_returns_the_work_to_composing(
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    lifecycle, _store, _artifacts, _github = await _lifecycle(
        dialect_engine, tmp_path, review_verdict="repair"
    )
    await lifecycle.start(**_start_kwargs())
    await lifecycle.resume(WORK_ID, project_id=PROJECT)
    record = await lifecycle.resume(WORK_ID, project_id=PROJECT)
    assert record.status == "COMPOSING"


@pytest.mark.asyncio
async def test_credential_values_never_reach_the_stored_artifact(
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    lifecycle, _store, artifacts, _github = await _lifecycle(
        dialect_engine,
        tmp_path,
        body="# Summary\n\nThe token is ghp_secret.\n",
        credential_values={"GITHUB_TOKEN": "ghp_secret"},
    )
    await lifecycle.start(**_start_kwargs())
    record = await lifecycle.resume(WORK_ID, project_id=PROJECT)
    archive = ReportArchive.model_validate(record.profile_context["report"]["archive"])
    stored = artifacts.read(archive.report_ref, project_id=PROJECT).decode()
    assert "ghp_secret" not in stored and "[REDACTED:GITHUB_TOKEN]" in stored


@pytest.mark.asyncio
async def test_failed_details_are_redacted_before_the_work_stream(
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    lifecycle, store, _artifacts, _github = await _lifecycle(
        dialect_engine,
        tmp_path,
        compose_status="failed",
        compose_summary="compose saw ghp_secret",
        credential_values={"GITHUB_TOKEN": "ghp_secret"},
    )
    await lifecycle.start(**_start_kwargs())
    await lifecycle.resume(WORK_ID, project_id=PROJECT)
    observation = next(
        event.payload_json
        for event in await store.read_events(WORK_ID, project_id=PROJECT)
        if event.event_type is WorkEventType.OBSERVATION_RECORDED
    )
    assert "ghp_secret" not in observation["detail"]
    assert "[REDACTED:GITHUB_TOKEN]" in observation["detail"]

    lifecycle, store, _artifacts, _github = await _lifecycle(
        dialect_engine,
        tmp_path,
        review_verdict="blocked",
        review_finding="review saw ghp_secret",
        credential_values={"GITHUB_TOKEN": "ghp_secret"},
    )
    await lifecycle.start(
        **{**_start_kwargs(), "work_id": "t1:report:1:s2", "source_ref": "report://t1/1/s2"}
    )
    await lifecycle.resume("t1:report:1:s2", project_id=PROJECT)
    await lifecycle.resume("t1:report:1:s2", project_id=PROJECT)
    blocked = next(
        event.payload_json
        for event in await store.read_events("t1:report:1:s2", project_id=PROJECT)
        if event.event_type is WorkEventType.WORK_BLOCKED
    )
    assert "ghp_secret" not in blocked["decision_request"]
    assert "[REDACTED:GITHUB_TOKEN]" in blocked["decision_request"]


@pytest.mark.asyncio
async def test_a_failed_console_delivery_post_check_blocks_the_work(
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    lifecycle, store, _artifacts, _github = await _lifecycle(dialect_engine, tmp_path)
    await lifecycle.start(**_start_kwargs())
    await lifecycle.resume(WORK_ID, project_id=PROJECT)
    record = await lifecycle.resume(WORK_ID, project_id=PROJECT)
    report = dict(record.profile_context["report"])
    archive = ReportArchive.model_validate(report["archive"])
    report["archive"] = archive.model_copy(update={"report_sha256": "0" * 64}).model_dump(
        mode="json"
    )
    await store.save_work(
        record.model_copy(
            update={"profile_context": {"task_id": TASK_ID, "cycle": 1, "report": report}}
        )
    )

    record, receipts = await lifecycle.deliver(WORK_ID, project_id=PROJECT, sink_version=1)

    assert record.status == "WORK_BLOCKED"
    assert receipts[0].result.status == "failed"
    blocked = next(
        event.payload_json
        for event in await store.read_events(WORK_ID, project_id=PROJECT)
        if event.event_type is WorkEventType.WORK_BLOCKED
    )
    assert blocked["reason"] == "report_delivery_post_check_failed"
    assert blocked["decision_request"] == receipts[0].observation["detail"]
    record, again = await lifecycle.deliver(WORK_ID, project_id=PROJECT, sink_version=1)
    assert record.status == "WORK_BLOCKED"
    assert again[0].result.status == "failed"
    assert len(record.profile_context["report"]["delivered"]) == 1


@pytest.mark.asyncio
async def test_a_replayed_delivery_posts_no_second_comment(
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    lifecycle, _store, _artifacts, github = await _lifecycle(
        dialect_engine, tmp_path, issue_sink=True
    )
    await lifecycle.start(**_start_kwargs(issue_sink=True))
    await lifecycle.resume(WORK_ID, project_id=PROJECT)
    await lifecycle.resume(WORK_ID, project_id=PROJECT)
    record, first = await lifecycle.deliver(WORK_ID, project_id=PROJECT, sink_version=3)
    assert record.status == "READY_TO_DELIVER"
    assert record.profile_context["report"]["pending_sink_version"] == 2
    assert record.profile_context["report"]["deliver_action"]["rollback"] == "delete_comment"
    assert record.profile_context["report"]["deliver_action"]["scope"] == ISSUE_URL
    _record, replay_first = await lifecycle.deliver(WORK_ID, project_id=PROJECT, sink_version=3)
    assert replay_first[0].action.rollback is None
    assert replay_first[0].action.scope == first[0].action.scope
    assert not github.comments
    record, github_receipts = await lifecycle.deliver(WORK_ID, project_id=PROJECT, sink_version=2)
    assert record.status == "COMPLETE"
    record, again = await lifecycle.deliver(WORK_ID, project_id=PROJECT, sink_version=2)

    assert len(github.comments) == 1
    assert again[0].result.external_ref == github_receipts[0].result.external_ref
    assert first[0].action.scope.startswith("artifact://")
    assert again[0].action.scope == ISSUE_URL
