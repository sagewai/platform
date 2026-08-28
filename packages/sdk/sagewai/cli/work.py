# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Direct local commands for the first software Work lifecycle."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import click
import httpx
from pydantic import ValidationError

import sagewai.cli as _cli
from sagewai import home
from sagewai.artifacts import LocalArtifactStore
from sagewai.core.context import ProjectContext, resolve_project_id
from sagewai.db import factory
from sagewai.fleet.execution import run_worker_subprocess
from sagewai.safety.permissions import PermissionPolicy
from sagewai.tools import factory as tool_factory
from sagewai.work import (
    CapabilityGrant,
    CapabilitySet,
    ClaudeRuntime,
    CodexRuntime,
    ControlDegradedError,
    OperatorController,
    PendingAttention,
    TaskCapsuleCompiler,
    WorkContract,
    WorkEventType,
    WorkItem,
    WorkMetrics,
    WorkRecord,
    WorkStore,
)
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.profiles.software import (
    SOFTWARE_WORKSPACE_CHECK_REF,
    BlastRadius,
    CatalogGitHubClient,
    CloudflareDeliveryConfig,
    CloudflareDeliveryControlProbe,
    CloudflareDocsAdapterConfig,
    CloudflareDocsDeliveryFlow,
    CloudflareDocsDeliveryPolicy,
    CloudflareDocsDeploymentProvider,
    CloudflareDocsObservationProvider,
    CloudflareDocsReleaseProvider,
    CloudflareRolloutStep,
    DeliveryActionDeniedError,
    DeliveryApprovalRequiredError,
    DeliveryLifecycle,
    GitHubIssueLifecycle,
    HealthGate,
    HealthVerdict,
    ReleaseCandidate,
    SoftwareContractContext,
    SoftwareLifecycle,
    SoftwareProfile,
    SoftwareReadOnlyResultValidator,
    SoftwareResultValidator,
    SoftwareStageOperator,
    SoftwareVerifier,
    SoftwareWorkspaceControlCheck,
    SoftwareWorktreeManager,
    WorktreeBranchPublisher,
    cloudflare_delivery_preconditions,
    cloudflare_version_digest,
    default_delivery_action_policy,
    github_remote_repository,
    is_github_issue_url,
)


@click.group()
def work() -> None:
    """Run deterministic local software work."""


@work.command("start")
@click.argument("description")
def work_start(description: str) -> None:
    """Start local software work from DESCRIPTION."""
    try:
        record = _cli._run_async(_start_work(description))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    _echo_record(record)


@work.command("status")
@click.argument("work_id")
def work_status(work_id: str) -> None:
    """Show the current state of WORK_ID."""
    record = _cli._run_async(_status_work(work_id))
    if record is None:
        raise click.ClickException(f"Work {work_id} not found")
    _echo_record(record)


@work.command("intake")
@click.option("--label", required=True, help="GitHub issue label to scan.")
def work_intake(label: str) -> None:
    """Start at most one unseen labeled issue from the local repository."""
    try:
        record = _cli._run_async(_intake_work(label))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    if record is None:
        click.echo(f"No unstarted issues in the oldest 100 open issues labeled {label}.")
        return
    _echo_record(record)


@work.command("resume")
@click.argument("work_id")
def work_resume(work_id: str) -> None:
    """Resume WORK_ID from durable canonical state."""
    try:
        record = _cli._run_async(_resume_work(work_id))
    except KeyError:
        raise click.ClickException(f"Work {work_id} not found") from None
    except (ControlDegradedError, DeliveryActionDeniedError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None
    _echo_record(record)


@work.command("approve")
@click.argument("work_id")
@click.argument("gate_id")
def work_approve(work_id: str, gate_id: str) -> None:
    """Approve one pending GATE_ID for WORK_ID."""
    try:
        record = _cli._run_async(_approve_work(work_id, gate_id))
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None
    _echo_record(record)


@work.command("pending")
def work_pending() -> None:
    """List canonical WorkItems that need operator attention."""
    pending = _cli._run_async(_pending_work())
    if not pending:
        click.echo("No pending Work attention.")
        return
    for item in pending:
        click.echo(f"{item.kind.value} {item.work_id} {item.attention_id}: {item.summary}")


@work.command("metrics")
@click.option("--work-id", default=None, help="Limit metrics to one WorkItem.")
@click.option("--profile", default=None, help="Limit metrics to one Work profile.")
@click.option("--runtime", default=None, help="Limit attributable metrics to one runtime.")
def work_metrics(
    work_id: str | None,
    profile: str | None,
    runtime: str | None,
) -> None:
    """Show read-only discipline and control metrics from Work events."""

    metrics = _cli._run_async(
        _work_metrics(work_id=work_id, profile=profile, runtime=runtime)
    )
    click.echo(json.dumps(metrics.model_dump(mode="json"), sort_keys=True))


def _echo_record(record: WorkRecord) -> None:
    click.echo(f"Work {record.work_id}: {record.status}")


async def _start_work(description: str) -> WorkRecord:
    project_id = resolve_project_id()
    repository, base_sha = await _repository_state()
    with ProjectContext(project_id=project_id):
        if is_github_issue_url(description):
            github = await _build_github_lifecycle(
                project_id=project_id,
                repository=repository,
            )
            return await github.start(
                issue_url=description,
                project_id=project_id,
                base_sha=base_sha,
            )

        lifecycle, _, _ = await _build_lifecycle(
            project_id=project_id,
            repository=repository,
        )
        now = datetime.now(timezone.utc)
        work_id = str(uuid.uuid4())
        work_item = WorkItem(
            id=work_id,
            project_id=project_id,
            profile="software",
            source="local",
            source_ref=None,
            title=description,
            description=description,
            target_systems=("repository",),
            created_at=now,
        )
        contract = WorkContract(
            id=str(uuid.uuid4()),
            project_id=project_id,
            work_id=work_id,
            version=1,
            goal=description,
            allowed_scope=(".",),
            acceptance_criteria=(description,),
            constraints=(),
            non_goals=(),
            evidence_refs=(),
            assumption_ids=(),
            risk="low",
            design_required=False,
            profile_context=SoftwareContractContext(
                base_sha=base_sha,
            ).model_dump(mode="json"),
        )
        return await lifecycle.start(work_item=work_item, contract=contract)


async def _status_work(work_id: str) -> WorkRecord | None:
    project_id = resolve_project_id()
    await factory.ensure_schema()
    store = WorkStore(engine=factory.get_engine())
    await store.init()
    return await store.load_work(work_id, project_id=project_id)


async def _intake_work(label: str) -> WorkRecord | None:
    label = label.strip()
    if not label:
        raise ValueError("GitHub intake label must not be empty")
    project_id = resolve_project_id()
    repository, base_sha = await _repository_state()
    owner, repo = await _repository_github_target(repository)
    with ProjectContext(project_id=project_id):
        lifecycle = await _build_github_lifecycle(
            project_id=project_id,
            repository=repository,
        )
        return await lifecycle.intake_labeled(
            owner=owner,
            repo=repo,
            label=label,
            project_id=project_id,
            base_sha=base_sha,
        )


async def _repository_github_target(repository: Path) -> tuple[str, str]:
    origin = await run_worker_subprocess(
        argv=("git", "remote", "get-url", "origin"),
        cwd=repository,
    )
    if origin.returncode != 0:
        raise ValueError(f"cannot read Git origin: {origin.stderr.strip()}")
    return github_remote_repository(origin.stdout.strip())


async def _resume_work(work_id: str) -> WorkRecord:
    project_id = resolve_project_id()
    record = await _status_work(work_id)
    if record is None:
        raise KeyError(work_id)
    if record.status == "COMPLETE":
        return record
    repository, _ = await _repository_state()
    with ProjectContext(project_id=project_id):
        if record.source_ref and is_github_issue_url(record.source_ref):
            if record.status in {
                "READY_TO_DELIVER",
                "RELEASING",
                "STAGING",
                "PRODUCTION_CANARY",
                "PRODUCTION_ROLLOUT",
                "SOAKING",
                "ROLLING_BACK",
            }:
                try:
                    return await _run_docs_delivery_with_pending(
                        record,
                        project_id=project_id,
                        repository=repository,
                    )
                except DeliveryApprovalRequiredError:
                    gated = await _status_work(work_id)
                    if gated is None:
                        raise KeyError(work_id)
                    return gated
            github = await _build_github_lifecycle(
                project_id=project_id,
                repository=repository,
            )
            return await github.resume(work_id, project_id=project_id)
        lifecycle, _, _ = await _build_lifecycle(
            project_id=project_id,
            repository=repository,
        )
        return await lifecycle.resume(work_id, project_id=project_id)


async def _approve_work(work_id: str, gate_id: str) -> WorkRecord:
    project_id = resolve_project_id()
    record = await _status_work(work_id)
    if record is None:
        raise KeyError(work_id)
    if record.status == "COMPLETE":
        return record
    if record.status == "TRIAGING":
        raise ValueError("cannot approve a stale gate from TRIAGING")
    if record.source_ref is None or not is_github_issue_url(record.source_ref):
        raise ValueError("merge approval requires GitHub-sourced Work")
    repository, _ = await _repository_state()
    with ProjectContext(project_id=project_id):
        await factory.ensure_schema()
        store = WorkStore(engine=factory.get_engine())
        await store.init()
        events = await store.read_events(work_id, project_id=project_id)
        requested = next(
            (
                event
                for event in reversed(events)
                if event.event_type is WorkEventType.GATE_REQUESTED
                and event.payload_json.get("gate_id") == gate_id
            ),
            None,
        )
        if requested is not None and requested.payload_json.get("action", {}).get("action") in {
            "deploy_production",
            "promote_rollout",
            "rollback",
        }:
            return await _run_docs_delivery_with_pending(
                record,
                project_id=project_id,
                repository=repository,
                approve_gate_id=gate_id,
            )
        github = await _build_github_lifecycle(
            project_id=project_id,
            repository=repository,
        )
        return await github.approve(
            work_id,
            project_id=project_id,
            gate_id=gate_id,
            actor_ref="cli",
        )


async def _pending_work() -> tuple[PendingAttention, ...]:
    project_id = resolve_project_id()
    await factory.ensure_schema()
    store = WorkStore(engine=factory.get_engine())
    await store.init()
    return await store.pending_attention(project_id=project_id)


async def _work_metrics(
    *,
    work_id: str | None = None,
    profile: str | None = None,
    runtime: str | None = None,
) -> WorkMetrics:
    project_id = resolve_project_id()
    await factory.ensure_schema()
    store = WorkStore(engine=factory.get_engine())
    await store.init()
    return await store.metrics(
        project_id=project_id,
        work_id=work_id,
        profile=profile,
        runtime=runtime,
    )


async def _build_lifecycle(
    *,
    project_id: str,
    repository: Path,
) -> tuple[SoftwareLifecycle, WorkStore, SoftwareWorktreeManager]:
    await factory.ensure_schema()
    engine = factory.get_engine()
    work_store = WorkStore(engine=engine)
    knowledge_store = KnowledgeStore(engine=engine)
    await work_store.init()
    await knowledge_store.init()
    durability_store = await factory.get_workflow_store()
    permission_policy = PermissionPolicy()

    implementation_controller = OperatorController(
        work_store=work_store,
        durability_store=durability_store,
        permission_policy=permission_policy,
        control_checks={
            SOFTWARE_WORKSPACE_CHECK_REF: SoftwareWorkspaceControlCheck(),
        },
        result_validator=SoftwareResultValidator(),
    )
    review_controller = OperatorController(
        work_store=work_store,
        durability_store=durability_store,
        permission_policy=permission_policy,
        control_checks={
            SOFTWARE_WORKSPACE_CHECK_REF: SoftwareWorkspaceControlCheck(),
        },
        result_validator=SoftwareReadOnlyResultValidator(),
    )
    write_capabilities = CapabilitySet(
        project_id=project_id,
        grants=(
            CapabilityGrant(
                project_id=project_id,
                name="filesystem.write",
                kind="filesystem",
                scope={"roots": ["."]},
                permissions=("workspace.read", "workspace.write"),
            ),
        ),
    )
    read_capabilities = CapabilitySet(
        project_id=project_id,
        grants=(
            CapabilityGrant(
                project_id=project_id,
                name="filesystem.read",
                kind="filesystem",
                scope={"roots": ["."]},
                permissions=("workspace.read",),
            ),
        ),
    )
    codex = CodexRuntime()
    claude = ClaudeRuntime()
    worktree_manager = SoftwareWorktreeManager()
    artifact_store = LocalArtifactStore()
    lifecycle = SoftwareLifecycle(
        profile=SoftwareProfile(),
        work_store=work_store,
        knowledge_store=knowledge_store,
        capsule_compiler=TaskCapsuleCompiler(
            knowledge_store=knowledge_store,
            artifact_store=artifact_store,
        ),
        worktree_manager=worktree_manager,
        verifier=SoftwareVerifier(
            knowledge_store=knowledge_store,
            artifact_store=artifact_store,
        ),
        artifact_store=artifact_store,
        repository=repository,
        analyst=SoftwareStageOperator(
            actor_ref="runtime:claude:analyst",
            runtime=claude,
            capabilities=read_capabilities,
            controller=review_controller,
        ),
        implementer=SoftwareStageOperator(
            actor_ref="runtime:codex:implementer",
            runtime=codex,
            capabilities=write_capabilities,
            controller=implementation_controller,
        ),
        reviewer=SoftwareStageOperator(
            actor_ref="runtime:claude:reviewer",
            runtime=claude,
            capabilities=read_capabilities,
            controller=review_controller,
        ),
        repairer=SoftwareStageOperator(
            actor_ref="runtime:codex:implementer",
            runtime=codex,
            capabilities=write_capabilities,
            controller=implementation_controller,
        ),
        repo_instructions=(("AGENTS.md",) if (repository / "AGENTS.md").is_file() else ()),
        verification_commands=("just smoke",),
    )
    return lifecycle, work_store, worktree_manager


async def _build_github_lifecycle(
    *,
    project_id: str,
    repository: Path,
) -> GitHubIssueLifecycle:
    lifecycle, work_store, worktree_manager = await _build_lifecycle(
        project_id=project_id,
        repository=repository,
    )
    callables = tool_factory.build_callables(
        project_id=project_id,
        get_credentials=_local_github_credentials,
    )
    return GitHubIssueLifecycle(
        work_store=work_store,
        software_lifecycle=lifecycle,
        github=CatalogGitHubClient(
            project_id=project_id,
            github_callable=callables["github"],
        ),
        branch_publisher=WorktreeBranchPublisher(
            worktree_manager=worktree_manager,
            repository=repository,
        ),
    )


def _local_github_credentials(**_kwargs) -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    if token is None or not token.strip():
        raise click.ClickException("GITHUB_TOKEN is required for GitHub Work")
    return {"GITHUB_TOKEN": token}


async def _run_docs_delivery_with_pending(
    record: WorkRecord,
    *,
    project_id: str,
    repository: Path,
    approve_gate_id: str | None = None,
) -> WorkRecord:
    try:
        result = await _run_docs_delivery(
            record,
            project_id=project_id,
            repository=repository,
            approve_gate_id=approve_gate_id,
        )
    except Exception as delivery_error:
        try:
            github = await _build_github_lifecycle(
                project_id=project_id,
                repository=repository,
            )
            await github.present_pending(record.work_id, project_id=project_id)
        except Exception:
            # Keep the delivery cause authoritative; Python records this failure as context.
            raise delivery_error
        raise

    github = await _build_github_lifecycle(
        project_id=project_id,
        repository=repository,
    )
    await github.present_pending(record.work_id, project_id=project_id)
    return result


async def _run_docs_delivery(
    record: WorkRecord,
    *,
    project_id: str,
    repository: Path,
    approve_gate_id: str | None = None,
    process_runner=run_worker_subprocess,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> WorkRecord:
    if not (repository / "apps" / "docs" / "wrangler.toml").is_file():
        raise ValueError("configured Sagewai docs delivery path is unavailable")
    settings = _docs_delivery_settings()
    github_context = record.profile_context.get("github", {})
    merged_sha = github_context.get("merged_sha")
    if not isinstance(merged_sha, str) or not merged_sha:
        raise ValueError("READY_TO_DELIVER Work has no canonical merged SHA")

    await factory.ensure_schema()
    store = WorkStore(engine=factory.get_engine())
    await store.init()
    events = await store.read_events(record.work_id, project_id=project_id)
    verification_event = next(
        (
            event
            for event in reversed(events)
            if event.event_type is WorkEventType.VERIFICATION_RECORDED
        ),
        None,
    )
    review_event = next(
        (event for event in reversed(events) if event.event_type is WorkEventType.REVIEW_RECORDED),
        None,
    )
    if verification_event is None or review_event is None:
        raise ValueError("delivery requires canonical verification and review evidence")

    adapter_config = CloudflareDocsAdapterConfig(
        project_id=project_id,
        work_id=record.work_id,
        repository_root=repository,
        docs_directory=repository / "apps" / "docs",
        artifact_root=home.data_dir() / "work" / "releases" / record.work_id,
        account_id=settings["account_id"],
        script_name="docs",
        target_url="https://docs.sagewai.ai",
        observation_sample_interval_seconds=settings["observation_sample_seconds"],
        command_timeout_seconds=settings["command_timeout_seconds"],
    )
    control_config = CloudflareDeliveryConfig(
        project_id=project_id,
        account_id=settings["account_id"],
        zone_name="sagewai.ai",
        script_name="docs",
        target_url="https://docs.sagewai.ai",
        minimum_credential_ttl_seconds=settings["minimum_credential_ttl_seconds"],
        maximum_monitoring_staleness_seconds=settings["maximum_monitoring_staleness_seconds"],
    )
    known_good_version = settings["known_good_version_id"]
    known_good = ReleaseCandidate(
        id=f"cloudflare-known-good-{known_good_version[:12]}",
        project_id=project_id,
        work_id=record.work_id,
        commit_sha=settings["known_good_commit_sha"],
        artifact_ref=f"cloudflare-version://{known_good_version}",
        artifact_digest=cloudflare_version_digest(
            settings["account_id"],
            "docs",
            known_good_version,
        ),
        config_revision=None,
        verification_ref=settings["known_good_verification_ref"],
        review_ref=settings["known_good_review_ref"],
    )
    token = settings["api_token"]
    async with httpx.AsyncClient(
        timeout=settings["http_timeout_seconds"],
        transport=http_transport,
    ) as client:
        lifecycle = DeliveryLifecycle(
            work_store=store,
            release_provider=CloudflareDocsReleaseProvider(
                config=adapter_config,
                process_runner=process_runner,
                verification_ref=f"work-event://{verification_event.id}",
                review_ref=f"work-event://{review_event.id}",
            ),
            deployment_provider=CloudflareDocsDeploymentProvider(
                config=adapter_config,
                api_token=token,
                client=client,
                process_runner=process_runner,
            ),
            observation_provider=CloudflareDocsObservationProvider(
                config=adapter_config,
                api_token=token,
                client=client,
            ),
            control_probe=CloudflareDeliveryControlProbe(
                config=control_config,
                api_token=token,
                client=client,
            ),
            control_preconditions=cloudflare_delivery_preconditions(project_id),
            action_policy=default_delivery_action_policy,
            heartbeat_interval=settings["heartbeat_seconds"],
        )
        flow = CloudflareDocsDeliveryFlow(
            work_store=store,
            lifecycle=lifecycle,
            policy=settings["policy"],
            known_good_candidate=known_good,
            health_gates=(
                HealthGate(
                    id="docs-http-availability",
                    project_id=project_id,
                    description="docs.sagewai.ai returns a successful HTTP response",
                    check_ref="https://docs.sagewai.ai",
                    failure_verdict=HealthVerdict.FAIL,
                ),
            ),
            merged_sha=merged_sha,
            release_evidence_refs=(
                f"work-event://{verification_event.id}",
                f"work-event://{review_event.id}",
                f"github-merge://{merged_sha}",
            ),
        )
        if approve_gate_id is not None:
            return await flow.approve(
                record.work_id,
                project_id=project_id,
                gate_id=approve_gate_id,
                actor_ref="cli",
            )
        return await flow.resume(record.work_id, project_id=project_id)


def _docs_delivery_settings() -> dict:
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
    values = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError("Cloudflare docs delivery configuration is missing: " + ", ".join(missing))
    try:
        rollout_payload = json.loads(values["SAGEWAI_DOCS_ROLLOUT_JSON"])
        policy = CloudflareDocsDeliveryPolicy(
            rollout=tuple(
                CloudflareRolloutStep(
                    exposure=BlastRadius(
                        dimension="traffic",
                        value=str(item["exposure"]),
                    ),
                    observation_window_seconds=int(item["observe_seconds"]),
                )
                for item in rollout_payload
            ),
            rollback_observation_window_seconds=int(
                values["SAGEWAI_DOCS_ROLLBACK_OBSERVATION_SECONDS"]
            ),
            evidence_ref=values["SAGEWAI_DOCS_POLICY_EVIDENCE_REF"],
        )
        return {
            "api_token": values["CLOUDFLARE_API_TOKEN"],
            "account_id": values["CLOUDFLARE_ACCOUNT_ID"],
            "known_good_version_id": values["SAGEWAI_DOCS_KNOWN_GOOD_VERSION_ID"],
            "known_good_commit_sha": values["SAGEWAI_DOCS_KNOWN_GOOD_COMMIT_SHA"],
            "known_good_verification_ref": values["SAGEWAI_DOCS_KNOWN_GOOD_VERIFICATION_REF"],
            "known_good_review_ref": values["SAGEWAI_DOCS_KNOWN_GOOD_REVIEW_REF"],
            "observation_sample_seconds": float(values["SAGEWAI_DOCS_OBSERVATION_SAMPLE_SECONDS"]),
            "command_timeout_seconds": float(values["SAGEWAI_DOCS_COMMAND_TIMEOUT_SECONDS"]),
            "http_timeout_seconds": float(values["SAGEWAI_DOCS_HTTP_TIMEOUT_SECONDS"]),
            "heartbeat_seconds": float(values["SAGEWAI_DOCS_HEARTBEAT_SECONDS"]),
            "minimum_credential_ttl_seconds": int(
                values["SAGEWAI_DOCS_MINIMUM_CREDENTIAL_TTL_SECONDS"]
            ),
            "maximum_monitoring_staleness_seconds": int(
                values["SAGEWAI_DOCS_MAXIMUM_MONITORING_STALENESS_SECONDS"]
            ),
            "policy": policy,
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Cloudflare docs delivery configuration is invalid: {exc}") from exc


async def _repository_state() -> tuple[Path, str]:
    root = await run_worker_subprocess(
        argv=("git", "rev-parse", "--show-toplevel"),
        cwd=Path.cwd(),
    )
    if root.returncode != 0:
        raise click.ClickException("Current directory is not a Git repository")
    repository = Path(root.stdout.strip()).resolve()
    revision = await run_worker_subprocess(
        argv=("git", "rev-parse", "HEAD"),
        cwd=repository,
    )
    if revision.returncode != 0:
        raise click.ClickException("Current repository has no readable HEAD")
    return repository, revision.stdout.strip()


__all__ = ["work"]
