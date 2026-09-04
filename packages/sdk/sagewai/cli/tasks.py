# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Headless Task commands: create one Task, run one coordinator tick."""

from __future__ import annotations

import os
from pathlib import Path

import click

import sagewai.cli as _cli
from sagewai.admin.channel_config_store import (
    AdminResourceChannelConfigStore,
    StateFileChannelConfigStore,
    org_for_project,
)
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.resource_stores import build_resource_stores
from sagewai.admin.state_file import AdminStateFile, default_admin_state_path
from sagewai.artifacts import LocalArtifactStore
from sagewai.connections.bootstrap import build_connections_context
from sagewai.db import factory
from sagewai.work.activity import WorkActivityStore
from sagewai.work.profiles.software.assembly import github_client_for
from sagewai.work.store import WorkStore
from sagewai.work.tasks.actions import RollbackExecutor
from sagewai.work.tasks.channels import (
    DecisionEscalation,
    GitHubIssueDecisionChannel,
    build_decision_channels,
)
from sagewai.work.tasks.coordinator import TaskCoordinator
from sagewai.work.tasks.decisions import DecisionChannel
from sagewai.work.tasks.models import TaskOrigin
from sagewai.work.tasks.report import ReportProfileRunner
from sagewai.work.tasks.runner import TaskCoordinatorRunner, interval_from_env, max_tasks_from_env
from sagewai.work.tasks.service import ClarificationDeadlines, TaskService
from sagewai.work.tasks.software import SoftwareProfileRunner
from sagewai.work.tasks.store import TaskStore
from sagewai.work.tasks.triggers import TriggerIntake


@click.group("task")
@click.option("--project", "project_scope", required=True, help="Project that owns the Tasks.")
@click.pass_context
def task_group(ctx: click.Context, project_scope: str) -> None:
    """Create and drive Tasks; the console and the API are the full surface."""
    if not project_scope or project_scope == "global":
        raise click.BadParameter("Tasks require an explicit project", param_hint="--project")
    ctx.obj = project_scope


@task_group.command("create")
@click.argument("brief", required=False)
@click.option(
    "--file",
    "brief_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read the brief from a file instead of an argument.",
)
@click.pass_obj
def task_create(project_id: str, brief: str | None, brief_file: Path | None) -> None:
    """Create one Task from BRIEF and print its id."""
    if (brief is None) == (brief_file is None):
        raise click.BadParameter("pass a brief argument or --file, not both")
    text = brief if brief is not None else brief_file.read_text(encoding="utf-8")
    task_id = _cli._run_async(_create(text, project_id=project_id))
    click.echo(task_id)


@task_group.command("tick")
@click.pass_obj
def task_tick(project_id: str) -> None:
    """Run one coordinator tick for this project and print how many Tasks were driven."""
    click.echo(_cli._run_async(_tick(project_id)))


async def _stores() -> tuple[TaskStore, WorkStore, WorkActivityStore]:
    await factory.ensure_schema()
    engine = factory.get_engine()
    return TaskStore(engine=engine), WorkStore(engine=engine), WorkActivityStore(engine=engine)


async def _create(brief: str, *, project_id: str) -> str:
    task_store, _work_store, _activity = await _stores()
    service = TaskService(store=task_store, artifact_store=LocalArtifactStore())
    task, _record = await service.create(
        brief, project_id=project_id, origin=TaskOrigin.HUMAN, created_by="cli"
    )
    return task.id


async def _config_store(project_id: str, state_file: AdminStateFile):
    """Where the coordinator reads chat webhook URLs for this project.

    A multi-tenant home keeps them in ``admin_resources``, tenant-key encrypted, written by the
    admin's channel routes; a single-org home keeps them in the same state file the admin's
    channel routes write. Both are read here, so a channel configured in the console works
    from the CLI too.
    """
    stores = await build_resource_stores(None)
    if stores is None:
        return StateFileChannelConfigStore(state_file=state_file)
    identity_store = IdentityStore(engine=factory.get_engine())
    org_id = await org_for_project(identity_store, project_id)
    if org_id is None:
        return StateFileChannelConfigStore(state_file=state_file)
    return AdminResourceChannelConfigStore(
        resource_store=stores.admin_resource, identity_store=identity_store, org_id=org_id
    )


async def _tick(project_id: str) -> int:
    task_store, work_store, activity_store = await _stores()
    service = TaskService(store=task_store, artifact_store=LocalArtifactStore())
    state_file = AdminStateFile(default_admin_state_path())
    connections = build_connections_context(state_file)
    software = SoftwareProfileRunner(
        work_store=work_store,
        github_factory=github_client_for,
        connection_store=connections.store,
        credentials=connections.router,
        stack_cache_limit=max(8, max_tasks_from_env()),
    )
    report = ReportProfileRunner(
        work_store=work_store,
        github_factory=github_client_for,
        connection_store=connections.store,
        credentials=connections.router,
        stack_cache_limit=max(8, max_tasks_from_env()),
    )
    defaults = await task_store.get_defaults(project_id=project_id)
    channels = await build_decision_channels(
        defaults=defaults,
        config_store=await _config_store(project_id, state_file),
        tracking_channel=GitHubIssueDecisionChannel(
            store=task_store, github_factory=github_client_for
        ),
        console_base_url=os.environ.get("SAGEWAI_CONSOLE_BASE_URL"),
    )

    async def _ready_channels(_project_id: str) -> tuple[DecisionChannel, ...]:
        """The CLI serves one project; resolver order keeps console first unless defaults differ."""
        return channels

    coordinator = TaskCoordinator(
        task_store=task_store,
        work_store=work_store,
        profile_runners=lambda task: report if task.profile == "report" else software,
        activity_store=activity_store,
        channel_factory=_ready_channels,
        rollbacks=RollbackExecutor(github_factory=github_client_for),
    )
    runner = TaskCoordinatorRunner(
        task_store=task_store,
        driver=coordinator,
        list_project_ids=lambda: _one(project_id),
        sweepers=(
            TriggerIntake(
                task_store=task_store,
                work_store=work_store,
                service=service,
                github_factory=github_client_for,
            ),
            ClarificationDeadlines(store=task_store, service=service),
            DecisionEscalation(store=task_store, channels=_ready_channels),
        ),
        interval_seconds=interval_from_env(),
        max_tasks=max_tasks_from_env(),
    )
    try:
        return await runner.tick()
    finally:
        try:
            await software.aclose()
        finally:
            await report.aclose()


async def _one(project_id: str) -> list[str]:
    return [project_id]


__all__ = ["task_group"]
