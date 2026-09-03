# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Assemble the software Work stack once, for the CLI and for the coordinator."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.artifacts import LocalArtifactStore
from sagewai.db import factory
from sagewai.fleet.registry import PostgresFleetRegistry
from sagewai.fleet.task_store import PostgresTaskStore
from sagewai.harness.discovery import discover_local_backends, openai_base_url
from sagewai.safety.permissions import PermissionPolicy
from sagewai.work.activity import WorkActivityStore
from sagewai.work.activity_ingestion import ActivityIngestion, BatchingActivitySink
from sagewai.work.capsule import TaskCapsuleCompiler
from sagewai.work.control import OperatorController
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.profiles.software.fleet_workspace import (
    SoftwareFleetWorkspaceTransport,
    software_repository_ref,
)
from sagewai.work.profiles.software.github import GitHubIssueLifecycle, WorktreeBranchPublisher
from sagewai.work.profiles.software.lifecycle import (
    SoftwareLifecycle,
    SoftwareStageOperator,
    StageOperatorLadder,
)
from sagewai.work.profiles.software.models import SoftwareRepositoryOutcome
from sagewai.work.profiles.software.profile import SoftwareProfile
from sagewai.work.profiles.software.scm import (
    SOFTWARE_WORKSPACE_CHECK_REF,
    SoftwareWorkspaceControlCheck,
    SoftwareWorktreeManager,
)
from sagewai.work.profiles.software.verification import (
    SandboxedVerificationRunner,
    SoftwareReadOnlyResultValidator,
    SoftwareResultValidator,
    SoftwareVerifier,
)
from sagewai.work.runtime import (
    CapabilityGrant,
    CapabilitySet,
    ClaudeRuntime,
    CodexRuntime,
    OperatorRuntime,
)
from sagewai.work.runtime_harness import HarnessRuntime
from sagewai.work.store import WorkStore
from sagewai.work.tasks.store import TaskStore

ControllerFactory = Callable[..., OperatorController]


def github_token_credentials(**_kwargs: object) -> dict[str, str]:
    """The GitHub credential the tool callable asks for, read from the process environment."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required for GitHub Work")
    return {"GITHUB_TOKEN": token}


@dataclass(frozen=True)
class SoftwareStack:
    """Everything one software drive needs, built from one engine and one repository."""

    lifecycle: SoftwareLifecycle
    work_store: WorkStore
    worktree_manager: SoftwareWorktreeManager
    activity_sink: BatchingActivitySink
    capsule_compiler: TaskCapsuleCompiler
    read_controller: OperatorController
    read_capabilities: CapabilitySet
    analysis_runtime: OperatorRuntime


async def build_software_stack(
    *,
    project_id: str | None,
    repository: Path,
    verification_image: str,
    verification_commands: tuple[str, ...] = ("just smoke",),
    execution: str = "local",
    fleet_org: str | None = None,
    prefer_free_implementation: bool = False,
    max_attempts_per_stage: int = 3,
    controller_factory: ControllerFactory = OperatorController,
    engine: AsyncEngine | None = None,
) -> SoftwareStack:
    """Build the ladders, controllers, verifier, and lifecycle for one repository."""
    if engine is None:
        await factory.ensure_schema()
        engine = factory.get_engine()
    work_store = WorkStore(engine=engine)
    knowledge_store = KnowledgeStore(engine=engine)
    task_store = TaskStore(engine=engine)
    activity_store = WorkActivityStore(engine=engine)
    await work_store.init()
    await knowledge_store.init()
    activity_sink = ActivityIngestion(
        work_store=work_store,
        task_store=task_store,
        activity_store=activity_store,
    ).sink()
    durability_store = await factory.get_workflow_store()
    permission_policy = PermissionPolicy()

    implementation_controller = controller_factory(
        work_store=work_store,
        durability_store=durability_store,
        permission_policy=permission_policy,
        control_checks={
            SOFTWARE_WORKSPACE_CHECK_REF: SoftwareWorkspaceControlCheck(),
        },
        result_validator=SoftwareResultValidator(),
    )
    review_controller = controller_factory(
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
            repository_ref=await software_repository_ref(repository),
        )

        def fleet_stage(
            *,
            actor_ref: str,
            runtime_capability: str,
            harness_tier: str | None = None,
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
                artifact_store=artifact_store,
                harness_tier=harness_tier,
                capabilities=capabilities,
                controller=controller,
            )

        analyst = fleet_stage(
            actor_ref="fleet:claude:analyst",
            runtime_capability="runtime.claude",
            capabilities=read_capabilities,
            controller=review_controller,
        )
        analysis_runtime = analyst.runtime
        implementer = fleet_stage(
            actor_ref="fleet:codex:implementer",
            runtime_capability="runtime.codex",
            capabilities=write_capabilities,
            controller=implementation_controller,
        )
        implementers = (implementer,)
        if prefer_free_implementation:
            implementers = (
                fleet_stage(
                    actor_ref="fleet:harness:implementer",
                    runtime_capability="runtime.harness",
                    harness_tier="complex",
                    capabilities=write_capabilities,
                    controller=implementation_controller,
                ),
                implementer,
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
        codex = CodexRuntime(activity_sink=activity_sink, artifact_store=artifact_store)
        claude = ClaudeRuntime(activity_sink=activity_sink, artifact_store=artifact_store)
        analysis_runtime = claude
        analyst = SoftwareStageOperator(
            actor_ref="runtime:claude:analyst",
            runtime=claude,
            capabilities=read_capabilities,
            controller=review_controller,
        )
        codex_implementer = SoftwareStageOperator(
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
        implementers = (codex_implementer,)
        if prefer_free_implementation:
            if project_id is None:
                raise ValueError("--prefer-free-implementation requires a project")
            defaults = await task_store.get_defaults(project_id=project_id)
            if "complex" not in defaults.harness_tiers:
                raise ValueError("configure harness tiers in task defaults")
            backends = {
                name: openai_base_url(discovered.openai_compat_url)
                for name, discovered in (await discover_local_backends()).items()
            }
            implementers = (
                SoftwareStageOperator(
                    actor_ref="runtime:harness:implementer",
                    runtime=HarnessRuntime(
                        tier="complex",
                        tiers=dict(defaults.harness_tiers),
                        backends=backends,
                        activity_sink=activity_sink,
                        artifact_store=artifact_store,
                    ),
                    capabilities=write_capabilities,
                    controller=implementation_controller,
                ),
                codex_implementer,
            )
    capsule_compiler = TaskCapsuleCompiler(
        knowledge_store=knowledge_store,
        artifact_store=artifact_store,
    )
    lifecycle = SoftwareLifecycle(
        profile=SoftwareProfile(),
        work_store=work_store,
        knowledge_store=knowledge_store,
        capsule_compiler=capsule_compiler,
        worktree_manager=worktree_manager,
        verifier=SoftwareVerifier(
            knowledge_store=knowledge_store,
            runner=SandboxedVerificationRunner(image=verification_image),
            artifact_store=artifact_store,
            activity_sink=activity_sink,
        ),
        artifact_store=artifact_store,
        repository=repository,
        analyst=StageOperatorLadder((analyst,)),
        designer=StageOperatorLadder((analyst,)),
        implementer=StageOperatorLadder(implementers),
        reviewer=StageOperatorLadder((reviewer,)),
        repairer=StageOperatorLadder((repairer,)),
        repo_instructions=(("AGENTS.md",) if (repository / "AGENTS.md").is_file() else ()),
        verification_commands=verification_commands,
        max_attempts_per_stage=max_attempts_per_stage,
    )
    return SoftwareStack(
        lifecycle=lifecycle,
        work_store=work_store,
        worktree_manager=worktree_manager,
        activity_sink=activity_sink,
        capsule_compiler=capsule_compiler,
        read_controller=review_controller,
        read_capabilities=read_capabilities,
        analysis_runtime=analysis_runtime,
    )


def build_github_lifecycle(
    stack: SoftwareStack,
    *,
    project_id: str,
    repository: Path,
    github,
    execution: str,
    fleet_org: str | None,
    merge_policy,
    task_id: str | None = None,
) -> GitHubIssueLifecycle:
    """Wrap one software stack in the GitHub issue, pull request, and merge boundary."""
    return GitHubIssueLifecycle(
        work_store=stack.work_store,
        software_lifecycle=stack.lifecycle,
        github=github,
        branch_publisher=WorktreeBranchPublisher(
            worktree_manager=stack.worktree_manager, repository=repository
        ),
        repository_outcome=SoftwareRepositoryOutcome.MERGED,
        execution_route=execution,
        fleet_org_id=fleet_org,
        merge_policy=merge_policy,
        task_id=task_id,
    )
