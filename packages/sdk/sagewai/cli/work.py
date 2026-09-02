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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click

import sagewai.cli as _cli
from sagewai.artifacts import LocalArtifactStore
from sagewai.db import factory
from sagewai.fleet.execution import run_worker_subprocess
from sagewai.fleet.registry import PostgresFleetRegistry
from sagewai.fleet.task_store import PostgresTaskStore
from sagewai.safety.permissions import PermissionPolicy
from sagewai.tools import factory as tool_factory
from sagewai.work import (
    AcceptanceCriterion,
    CapabilityGrant,
    CapabilitySet,
    ClaudeRuntime,
    CodexRuntime,
    ControlDegradedError,
    OperatorController,
    PendingAttention,
    TaskCapsuleCompiler,
    WorkContract,
    WorkEvent,
    WorkEventType,
    WorkItem,
    WorkMetrics,
    WorkRecord,
    WorkStore,
)
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.profiles.software import (
    SOFTWARE_WORKSPACE_CHECK_REF,
    CatalogGitHubClient,
    GitHubIssueLifecycle,
    SandboxedVerificationRunner,
    SoftwareContractContext,
    SoftwareLifecycle,
    SoftwareProfile,
    SoftwareReadOnlyResultValidator,
    SoftwareRepositoryOutcome,
    SoftwareResultValidator,
    SoftwareStageOperator,
    SoftwareVerifier,
    SoftwareWorkspaceControlCheck,
    SoftwareWorktreeManager,
    StageOperatorLadder,
    WorktreeBranchPublisher,
    github_remote_repository,
    is_github_issue_url,
)
from sagewai.work.profiles.software.fleet_workspace import (
    SoftwareFleetWorkspaceTransport,
)
from sagewai.work.profiles.software.fleet_workspace import (
    software_repository_ref as _software_repository_ref,
)


@click.group()
@click.option(
    "--project",
    "project_scope",
    required=True,
    metavar="<slug|global>",
    help="Project slug, or 'global' for organization-global Work.",
)
@click.option(
    "--execution",
    type=click.Choice(("local", "fleet")),
    default="local",
    show_default=True,
    help="Execute operator stages locally or through registered Fleet workers.",
)
@click.option(
    "--fleet-org",
    default=None,
    metavar="ORG_ID",
    help="Fleet organization ID (required with --execution fleet).",
)
@click.pass_context
def work(
    ctx: click.Context,
    project_scope: str,
    execution: str,
    fleet_org: str | None,
) -> None:
    """Run deterministic software work through the selected execution path."""
    if not project_scope:
        raise click.BadParameter("project scope must not be empty", param_hint="--project")
    if execution == "fleet" and not fleet_org:
        raise click.BadParameter(
            "--fleet-org is required with --execution fleet",
            param_hint="--fleet-org",
        )
    if execution == "local" and fleet_org is not None:
        raise click.BadParameter(
            "--fleet-org requires --execution fleet",
            param_hint="--fleet-org",
        )
    ctx.obj = None if project_scope == "global" else project_scope


def _work_execution_config() -> tuple[str, str | None]:
    """Return the explicit Work-group execution selection for this command."""
    context = click.get_current_context(silent=True)
    while context is not None:
        if "execution" in context.params:
            return (
                str(context.params["execution"]),
                context.params.get("fleet_org"),
            )
        context = context.parent
    return "local", None


@work.command("start")
@click.argument("description")
@click.pass_obj
def work_start(project_id: str | None, description: str) -> None:
    """Start local software work from DESCRIPTION."""
    try:
        record = _cli._run_async(_start_work(description, project_id=project_id))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    _echo_record(record)


@work.command("status")
@click.argument("work_id")
@click.pass_obj
def work_status(project_id: str | None, work_id: str) -> None:
    """Show the current state of WORK_ID."""
    record = _cli._run_async(_status_work(work_id, project_id=project_id))
    if record is None:
        raise click.ClickException(f"Work {work_id} not found")
    _echo_record(record)


@work.command("intake")
@click.option("--label", required=True, help="GitHub issue label to scan.")
@click.pass_obj
def work_intake(project_id: str | None, label: str) -> None:
    """Start at most one unseen labeled issue from the local repository."""
    try:
        record = _cli._run_async(_intake_work(label, project_id=project_id))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    if record is None:
        click.echo(f"No unstarted issues in the oldest 100 open issues labeled {label}.")
        return
    _echo_record(record)


@work.command("resume")
@click.argument("work_id")
@click.pass_obj
def work_resume(project_id: str | None, work_id: str) -> None:
    """Resume WORK_ID from durable canonical state."""
    try:
        record = _cli._run_async(_resume_work(work_id, project_id=project_id))
    except KeyError:
        raise click.ClickException(f"Work {work_id} not found") from None
    except (ControlDegradedError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None
    _echo_record(record)


@work.command("approve")
@click.argument("work_id")
@click.argument("gate_id")
@click.pass_obj
def work_approve(project_id: str | None, work_id: str, gate_id: str) -> None:
    """Approve one pending GATE_ID for WORK_ID."""
    try:
        record = _cli._run_async(
            _approve_work(work_id, gate_id, project_id=project_id)
        )
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None
    _echo_record(record)


@work.command("pending")
@click.pass_obj
def work_pending(project_id: str | None) -> None:
    """List canonical WorkItems that need operator attention."""
    pending = _cli._run_async(_pending_work(project_id=project_id))
    if not pending:
        click.echo("No pending Work attention.")
        return
    for item in pending:
        click.echo(f"{item.kind.value} {item.work_id} {item.attention_id}: {item.summary}")


@work.command("metrics")
@click.option("--work-id", default=None, help="Limit metrics to one WorkItem.")
@click.option("--profile", default=None, help="Limit metrics to one Work profile.")
@click.option("--runtime", default=None, help="Limit attributable metrics to one runtime.")
@click.pass_obj
def work_metrics(
    project_id: str | None,
    work_id: str | None,
    profile: str | None,
    runtime: str | None,
) -> None:
    """Show read-only discipline and control metrics from Work events."""

    metrics = _cli._run_async(
        _work_metrics(
            project_id=project_id,
            work_id=work_id,
            profile=profile,
            runtime=runtime,
        )
    )
    click.echo(json.dumps(metrics.model_dump(mode="json"), sort_keys=True))


def _echo_record(record: WorkRecord) -> None:
    click.echo(f"Work {record.work_id}: {record.status}")


async def _start_work(
    description: str,
    *,
    project_id: str | None,
) -> WorkRecord:
    repository, base_sha = await _repository_state()
    execution, fleet_org = _work_execution_config()
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
        execution=execution,
        fleet_org=fleet_org,
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
    contract_id = str(uuid.uuid4())
    repository_criterion_id = f"{contract_id}:repository"
    contract = WorkContract(
        id=contract_id,
        project_id=project_id,
        work_id=work_id,
        version=1,
        goal=description,
        allowed_scope=(".",),
        acceptance_criteria=(
            AcceptanceCriterion(
                id=repository_criterion_id,
                project_id=project_id,
                statement="produce the accepted repository outcome",
                verification_kind="profile",
            ),
        ),
        constraints=(),
        non_goals=(),
        evidence_refs=(),
        assumption_ids=(),
        risk="low",
        design_required=False,
        profile_context=SoftwareContractContext(
            project_id=project_id,
            base_sha=base_sha,
            repository_outcome=SoftwareRepositoryOutcome.VERIFIED_COMMIT,
            repository_criterion_id=repository_criterion_id,
            delivery=None,
            execution_route=execution,
            fleet_org_id=fleet_org,
        ).model_dump(mode="json"),
    )
    return await lifecycle.start(work_item=work_item, contract=contract)


async def _status_work(
    work_id: str,
    *,
    project_id: str | None,
) -> WorkRecord | None:
    await factory.ensure_schema()
    store = WorkStore(engine=factory.get_engine())
    await store.init()
    return await store.load_work(work_id, project_id=project_id)


def _execution_route_from_events(
    events: list[WorkEvent],
) -> tuple[str, str | None]:
    contract_event = next(
        (
            event
            for event in reversed(events)
            if event.event_type
            in {WorkEventType.CONTRACT_ACCEPTED, WorkEventType.CONTRACT_PROPOSED}
        ),
        None,
    )
    if contract_event is None:
        raise ValueError("Work has no durable software contract")
    contract = WorkContract.model_validate(contract_event.payload_json)
    context = SoftwareContractContext.model_validate(contract.profile_context)
    if context.execution_route is None:
        raise ValueError("Work has no durable execution route and cannot be resumed safely")
    return context.execution_route, context.fleet_org_id


async def _stored_work_execution_route(
    work_id: str,
    *,
    project_id: str | None,
) -> tuple[str, str | None]:
    await factory.ensure_schema()
    store = WorkStore(engine=factory.get_engine())
    await store.init()
    events = await store.read_events(work_id, project_id=project_id)
    return _execution_route_from_events(events)


async def _intake_work(
    label: str,
    *,
    project_id: str | None,
) -> WorkRecord | None:
    label = label.strip()
    if not label:
        raise ValueError("GitHub intake label must not be empty")
    repository, base_sha = await _repository_state()
    owner, repo = await _repository_github_target(repository)
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


async def _resume_work(
    work_id: str,
    *,
    project_id: str | None,
) -> WorkRecord:
    record = await _status_work(work_id, project_id=project_id)
    if record is None:
        raise KeyError(work_id)
    if record.status in {
        "COMPLETE",
        "READY_TO_DELIVER",
        "RELEASING",
        "STAGING",
        "PRODUCTION_CANARY",
        "PRODUCTION_ROLLOUT",
        "SOAKING",
        "ROLLING_BACK",
    }:
        return record
    expected_execution, expected_fleet_org = await _stored_work_execution_route(
        work_id,
        project_id=project_id,
    )
    selected_execution, selected_fleet_org = _work_execution_config()
    if (selected_execution, selected_fleet_org) != (
        expected_execution,
        expected_fleet_org,
    ):
        if expected_execution == "fleet":
            raise ValueError(
                f"Work {work_id} is bound to fleet execution for organization "
                f"{expected_fleet_org}; resume with --execution fleet "
                f"--fleet-org {expected_fleet_org}"
            )
        raise ValueError(
            f"Work {work_id} is bound to local execution; resume without "
            "--execution fleet or --fleet-org"
        )
    repository, _ = await _repository_state()
    if record.source_ref and is_github_issue_url(record.source_ref):
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


async def _approve_work(
    work_id: str,
    gate_id: str,
    *,
    project_id: str | None,
) -> WorkRecord:
    record = await _status_work(work_id, project_id=project_id)
    if record is None:
        raise KeyError(work_id)
    if record.status == "COMPLETE":
        return record
    if record.status == "TRIAGING":
        raise ValueError("cannot approve a stale gate from TRIAGING")
    if record.source_ref is None or not is_github_issue_url(record.source_ref):
        raise ValueError("merge approval requires GitHub-sourced Work")
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
    if requested is not None and requested.payload_json.get("action", {}).get(
        "action"
    ) in {
        "deploy_production",
        "promote_rollout",
        "rollback",
    }:
        raise ValueError(
            "delivery approval requires an explicitly selected adapter"
        )
    repository, _ = await _repository_state()
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


async def _pending_work(
    *,
    project_id: str | None,
) -> tuple[PendingAttention, ...]:
    await factory.ensure_schema()
    store = WorkStore(engine=factory.get_engine())
    await store.init()
    return await store.pending_attention(project_id=project_id)


async def _work_metrics(
    *,
    project_id: str | None,
    work_id: str | None = None,
    profile: str | None = None,
    runtime: str | None = None,
) -> WorkMetrics:
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
    project_id: str | None,
    repository: Path,
    execution: str | None = None,
    fleet_org: str | None = None,
) -> tuple[SoftwareLifecycle, WorkStore, SoftwareWorktreeManager]:
    await factory.ensure_schema()
    engine = factory.get_engine()
    if execution is None:
        execution, selected_fleet_org = _work_execution_config()
        if fleet_org is None:
            fleet_org = selected_fleet_org
    if execution not in {"local", "fleet"}:
        raise ValueError(f"unsupported Work execution path: {execution}")
    if execution == "fleet" and not fleet_org:
        raise ValueError("fleet execution requires an explicit Fleet organization ID")
    if execution == "local" and fleet_org is not None:
        raise ValueError("Fleet organization ID requires fleet execution")
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
    worktree_manager = SoftwareWorktreeManager()
    artifact_store = LocalArtifactStore()
    if execution == "fleet":
        assert fleet_org is not None
        fleet_registry = PostgresFleetRegistry(engine=engine)
        fleet_store = PostgresTaskStore(engine=engine)
        await fleet_registry.init()
        await fleet_store.init()
        workspace_transport = SoftwareFleetWorkspaceTransport(
            repository_ref=await _software_repository_ref(repository),
        )

        def fleet_stage(
            *,
            actor_ref: str,
            runtime_capability: str,
            capabilities: CapabilitySet,
            controller: OperatorController,
        ) -> SoftwareStageOperator:
            return SoftwareStageOperator.fleet(
                actor_ref=actor_ref,
                store=fleet_store,
                registry=fleet_registry,
                org_id=fleet_org,
                runtime_capability=runtime_capability,
                poll_interval_seconds=0.25,
                heartbeat_ttl=timedelta(seconds=30),
                workspace_transport=workspace_transport,
                capabilities=capabilities,
                controller=controller,
            )

        analyst = fleet_stage(
            actor_ref="fleet:claude:analyst",
            runtime_capability="runtime.claude",
            capabilities=read_capabilities,
            controller=review_controller,
        )
        implementer = fleet_stage(
            actor_ref="fleet:codex:implementer",
            runtime_capability="runtime.codex",
            capabilities=write_capabilities,
            controller=implementation_controller,
        )
        reviewer = fleet_stage(
            actor_ref="fleet:claude:reviewer",
            runtime_capability="runtime.claude",
            capabilities=read_capabilities,
            controller=review_controller,
        )
        repairer = fleet_stage(
            actor_ref="fleet:codex:repairer",
            runtime_capability="runtime.codex",
            capabilities=write_capabilities,
            controller=implementation_controller,
        )
    else:
        codex = CodexRuntime()
        claude = ClaudeRuntime()
        analyst = SoftwareStageOperator(
            actor_ref="runtime:claude:analyst",
            runtime=claude,
            capabilities=read_capabilities,
            controller=review_controller,
        )
        implementer = SoftwareStageOperator(
            actor_ref="runtime:codex:implementer",
            runtime=codex,
            capabilities=write_capabilities,
            controller=implementation_controller,
        )
        reviewer = SoftwareStageOperator(
            actor_ref="runtime:claude:reviewer",
            runtime=claude,
            capabilities=read_capabilities,
            controller=review_controller,
        )
        repairer = SoftwareStageOperator(
            actor_ref="runtime:codex:implementer",
            runtime=codex,
            capabilities=write_capabilities,
            controller=implementation_controller,
        )
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
            runner=SandboxedVerificationRunner(image=_verification_image()),
            artifact_store=artifact_store,
        ),
        artifact_store=artifact_store,
        repository=repository,
        analyst=StageOperatorLadder((analyst,)),
        designer=StageOperatorLadder((analyst,)),
        implementer=StageOperatorLadder((implementer,)),
        reviewer=StageOperatorLadder((reviewer,)),
        repairer=StageOperatorLadder((repairer,)),
        repo_instructions=(("AGENTS.md",) if (repository / "AGENTS.md").is_file() else ()),
        verification_commands=("just smoke",),
    )
    return lifecycle, work_store, worktree_manager


def _verification_image() -> str:
    image = os.environ.get("SAGEWAI_WORK_VERIFICATION_IMAGE", "").strip()
    if not image:
        raise ValueError(
            "SAGEWAI_WORK_VERIFICATION_IMAGE is required; "
            "software verification never executes on the host"
        )
    return image


async def _build_github_lifecycle(
    *,
    project_id: str | None,
    repository: Path,
) -> GitHubIssueLifecycle:
    if project_id is None:
        raise ValueError("GitHub software lifecycle requires a project")
    execution, fleet_org = _work_execution_config()
    lifecycle, work_store, worktree_manager = await _build_lifecycle(
        project_id=project_id,
        repository=repository,
        execution=execution,
        fleet_org=fleet_org,
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
        repository_outcome=SoftwareRepositoryOutcome.MERGED,
        execution_route=execution,
        fleet_org_id=fleet_org,
    )


def _local_github_credentials(**_kwargs) -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    if token is None or not token.strip():
        raise click.ClickException("GITHUB_TOKEN is required for GitHub Work")
    return {"GITHUB_TOKEN": token}


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
