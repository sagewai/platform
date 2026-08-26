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

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import click

import sagewai.cli as _cli
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
    OperatorController,
    PendingAttention,
    TaskCapsuleCompiler,
    WorkContract,
    WorkItem,
    WorkRecord,
    WorkStore,
)
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.profiles.software import (
    CatalogGitHubClient,
    GitHubIssueLifecycle,
    SoftwareContractContext,
    SoftwareLifecycle,
    SoftwareReadOnlyResultValidator,
    SoftwareResultValidator,
    SoftwareStageOperator,
    SoftwareVerifier,
    SoftwareWorktreeManager,
    WorktreeBranchPublisher,
    is_github_issue_url,
)


@click.group()
def work() -> None:
    """Run deterministic local software work."""


@work.command("start")
@click.argument("description")
def work_start(description: str) -> None:
    """Start local software work from DESCRIPTION."""
    record = _cli._run_async(_start_work(description))
    _echo_record(record)


@work.command("status")
@click.argument("work_id")
def work_status(work_id: str) -> None:
    """Show the current state of WORK_ID."""
    record = _cli._run_async(_status_work(work_id))
    if record is None:
        raise click.ClickException(f"Work {work_id} not found")
    _echo_record(record)


@work.command("resume")
@click.argument("work_id")
def work_resume(work_id: str) -> None:
    """Resume WORK_ID from durable canonical state."""
    try:
        record = _cli._run_async(_resume_work(work_id))
    except KeyError:
        raise click.ClickException(f"Work {work_id} not found") from None
    _echo_record(record)


@work.command("approve")
@click.argument("work_id")
@click.argument("gate_id")
def work_approve(work_id: str, gate_id: str) -> None:
    """Approve one pending merge GATE_ID for WORK_ID."""
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


async def _resume_work(work_id: str) -> WorkRecord:
    project_id = resolve_project_id()
    record = await _status_work(work_id)
    if record is None:
        raise KeyError(work_id)
    repository, _ = await _repository_state()
    with ProjectContext(project_id=project_id):
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


async def _approve_work(work_id: str, gate_id: str) -> WorkRecord:
    project_id = resolve_project_id()
    record = await _status_work(work_id)
    if record is None:
        raise KeyError(work_id)
    if record.source_ref is None or not is_github_issue_url(record.source_ref):
        raise ValueError("merge approval requires GitHub-sourced Work")
    repository, _ = await _repository_state()
    with ProjectContext(project_id=project_id):
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
        control_checks={},
        result_validator=SoftwareResultValidator(),
    )
    review_controller = OperatorController(
        work_store=work_store,
        durability_store=durability_store,
        permission_policy=permission_policy,
        control_checks={},
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
    worktree_manager = SoftwareWorktreeManager()
    lifecycle = SoftwareLifecycle(
        work_store=work_store,
        capsule_compiler=TaskCapsuleCompiler(knowledge_store=knowledge_store),
        worktree_manager=worktree_manager,
        verifier=SoftwareVerifier(knowledge_store=knowledge_store),
        repository=repository,
        implementer=SoftwareStageOperator(
            actor_ref="runtime:codex:implementer",
            runtime=codex,
            capabilities=write_capabilities,
            controller=implementation_controller,
        ),
        reviewer=SoftwareStageOperator(
            actor_ref="runtime:claude:reviewer",
            runtime=ClaudeRuntime(),
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
    return {"GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN", "")}


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
