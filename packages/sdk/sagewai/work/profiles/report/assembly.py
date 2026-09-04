# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Assemble the report Work stack once, for the CLI and for the coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.artifacts import LocalArtifactStore
from sagewai.db import factory
from sagewai.harness.discovery import discover_local_backends, openai_base_url
from sagewai.safety.permissions import PermissionPolicy
from sagewai.sandbox.backend import SandboxBackend
from sagewai.work.activity import WorkActivityStore
from sagewai.work.activity_ingestion import ActivityIngestion, BatchingActivitySink
from sagewai.work.capsule import TaskCapsuleCompiler
from sagewai.work.control import OperatorController
from sagewai.work.harness_mcp import mcp_connection_resolver
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.profiles.report.lifecycle import ReportLifecycle, ReportOperator
from sagewai.work.profiles.report.profile import ReportProfile
from sagewai.work.profiles.report.sinks import ConsoleSink, GitHubIssueSink
from sagewai.work.profiles.software.assembly import ControllerFactory
from sagewai.work.profiles.software.github import GitHubClient
from sagewai.work.runtime import (
    CapabilityGrant,
    CapabilitySet,
    ClaudeRuntime,
    OperatorRuntime,
)
from sagewai.work.runtime_harness import HarnessRuntime
from sagewai.work.store import WorkStore
from sagewai.work.tasks.models import HarnessTier, ReportTarget
from sagewai.work.tasks.scratch import ScratchResultValidator, ScratchWorkspaceManager
from sagewai.work.tasks.store import TaskStore


@dataclass(frozen=True)
class ReportStack:
    lifecycle: ReportLifecycle
    work_store: WorkStore
    scratch_manager: ScratchWorkspaceManager
    activity_sink: BatchingActivitySink
    capsule_compiler: TaskCapsuleCompiler
    read_controller: OperatorController
    read_capabilities: CapabilitySet
    analysis_runtime: OperatorRuntime


async def build_report_stack(
    *,
    project_id: str,
    target: ReportTarget,
    harness_tiers: Mapping[str, HarnessTier],
    github: GitHubClient | None = None,
    controller_factory: ControllerFactory = OperatorController,
    engine: AsyncEngine | None = None,
    sandbox: SandboxBackend | None = None,
    connection_store: Any = None,
    credentials: Any = None,
    credential_values: Mapping[str, str] | None = None,
) -> ReportStack:
    """Every controller in this stack validates against a scratch workspace."""
    if engine is None:
        await factory.ensure_schema()
        engine = factory.get_engine()
    work_store = WorkStore(engine=engine)
    knowledge_store = KnowledgeStore(engine=engine)
    task_store = TaskStore(engine=engine)
    activity_store = WorkActivityStore(engine=engine)
    await work_store.init()
    await knowledge_store.init()
    artifact_store = LocalArtifactStore()
    activity_sink = ActivityIngestion(
        work_store=work_store,
        task_store=task_store,
        activity_store=activity_store,
    ).sink()
    durability_store = await factory.get_workflow_store()
    permission_policy = PermissionPolicy()

    def _controller() -> OperatorController:
        """No control checks: a scratch workspace has no worktree precondition."""
        return controller_factory(
            work_store=work_store,
            durability_store=durability_store,
            permission_policy=permission_policy,
            control_checks={},
            result_validator=ScratchResultValidator(),
        )

    compose_capabilities = CapabilitySet(
        project_id=project_id,
        grants=(
            *target.sources,
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
    resolved = dict(credential_values or {})
    analysis_runtime = ClaudeRuntime(activity_sink=activity_sink, artifact_store=artifact_store)
    composers: list[ReportOperator] = []
    medium = harness_tiers.get("medium")
    if medium is not None:
        backends = {
            name: openai_base_url(discovered.openai_compat_url)
            for name, discovered in (await discover_local_backends()).items()
        }
        if medium.backend in backends:
            composers.append(
                ReportOperator(
                    actor_ref="runtime:harness:medium",
                    runtime=HarnessRuntime(
                        tier="medium",
                        tiers=dict(harness_tiers),
                        backends=backends,
                        sandbox=sandbox,
                        mcp_connections=(
                            mcp_connection_resolver(
                                project_id=project_id,
                                connection_store=connection_store,
                                credentials=credentials,
                            )
                            if connection_store is not None
                            else None
                        ),
                        credential_values=resolved,
                        activity_sink=activity_sink,
                        artifact_store=artifact_store,
                    ),
                    controller=_controller(),
                    capabilities=compose_capabilities,
                )
            )
    composers.append(
        ReportOperator(
            actor_ref="runtime:claude:analysis",
            runtime=analysis_runtime,
            controller=_controller(),
            capabilities=compose_capabilities,
        )
    )
    sinks: dict[str, Any] = {"console": ConsoleSink(artifact_store=artifact_store)}
    if any(sink.kind == "github_issue" for sink in target.sinks):
        if github is None:
            raise ValueError("a github_issue sink needs a GitHub client")
        sinks["github_issue"] = GitHubIssueSink(github=github)
    scratch_manager = ScratchWorkspaceManager()
    capsule_compiler = TaskCapsuleCompiler(
        knowledge_store=knowledge_store,
        artifact_store=artifact_store,
    )
    return ReportStack(
        lifecycle=ReportLifecycle(
            profile=ReportProfile(),
            work_store=work_store,
            capsule_compiler=capsule_compiler,
            scratch_manager=scratch_manager,
            artifact_store=artifact_store,
            composer=tuple(composers),
            reviewer=(
                ReportOperator(
                    actor_ref="runtime:claude:review",
                    runtime=analysis_runtime,
                    controller=_controller(),
                    capabilities=read_capabilities,
                ),
            ),
            sinks=sinks,
            credential_values=resolved,
        ),
        work_store=work_store,
        scratch_manager=scratch_manager,
        activity_sink=activity_sink,
        capsule_compiler=capsule_compiler,
        read_controller=_controller(),
        read_capabilities=read_capabilities,
        analysis_runtime=analysis_runtime,
    )
