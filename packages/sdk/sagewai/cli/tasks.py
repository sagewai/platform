# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Headless Task commands for reading, creating, and driving Tasks."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import click
from pydantic import ValidationError

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
from sagewai.work.tasks.inbox import DecisionItem, decision_inbox
from sagewai.work.tasks.intake import IntakeResult
from sagewai.work.tasks.intake import route as intake_route
from sagewai.work.tasks.models import (
    BoardColumn,
    TaskKind,
    TaskOrigin,
    TaskRecord,
    TaskStatus,
    TaskTriggerSpec,
)
from sagewai.work.tasks.report import ReportProfileRunner
from sagewai.work.tasks.runner import TaskCoordinatorRunner, interval_from_env, max_tasks_from_env
from sagewai.work.tasks.service import (
    ClarificationDeadlines,
    TaskDecisionError,
    TaskNotFoundError,
    TaskService,
)
from sagewai.work.tasks.software import SoftwareProfileRunner
from sagewai.work.tasks.store import StaleTaskError, TaskStore
from sagewai.work.tasks.templates import CATALOGUE
from sagewai.work.tasks.transitions import IllegalTransitionError
from sagewai.work.tasks.triggers import TriggerIntake
from sagewai.work.tasks.views import ThreadView, thread_from_events


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


@task_group.command("list")
@click.option(
    "--status", "statuses", multiple=True, type=click.Choice([s.value for s in TaskStatus])
)
@click.option("--kind", "kinds", multiple=True, type=click.Choice([k.value for k in TaskKind]))
@click.option(
    "--origin", "origins", multiple=True, type=click.Choice([o.value for o in TaskOrigin])
)
@click.option(
    "--column", "columns", multiple=True, type=click.Choice([c.value for c in BoardColumn])
)
@click.option("--limit", default=50, show_default=True, type=click.IntRange(1, 200))
@click.pass_obj
def task_list(
    project_id: str,
    statuses: tuple[str, ...],
    kinds: tuple[str, ...],
    origins: tuple[str, ...],
    columns: tuple[str, ...],
    limit: int,
) -> None:
    """List this project's Tasks, oldest first."""
    records = _cli._run_async(
        _list(
            project_id,
            statuses=tuple(TaskStatus(value) for value in statuses) or None,
            kinds=tuple(TaskKind(value) for value in kinds) or None,
            origins=tuple(TaskOrigin(value) for value in origins) or None,
            board_columns=tuple(BoardColumn(value) for value in columns) or None,
            limit=limit,
        )
    )
    for record in records:
        click.echo(
            f"{record.task_id} {record.status.value} {record.board_column.value}: {record.title}"
        )


@task_group.command("board")
@click.pass_obj
def task_board(project_id: str) -> None:
    """Group this project's Tasks into the five board columns, newest-touched first."""
    records = _cli._run_async(_list(project_id, limit=200, order_by="updated_at", descending=True))
    grouped: dict[str, list[TaskRecord]] = {column.value: [] for column in BoardColumn}
    for record in records:
        grouped[record.board_column.value].append(record)
    for column, column_records in grouped.items():
        click.echo(f"{column}:")
        for record in column_records:
            click.echo(f"  {record.task_id} {record.status.value}: {record.title}")


@task_group.command("status")
@click.argument("task_id")
@click.pass_obj
def task_status(project_id: str, task_id: str) -> None:
    """Show the current state of TASK_ID."""
    record = _cli._run_async(_load_record(task_id, project_id=project_id))
    if record is None:
        raise click.ClickException(f"Task {task_id} not found")
    _echo_record(record)


@task_group.command("thread")
@click.argument("task_id")
@click.pass_obj
def task_thread(project_id: str, task_id: str) -> None:
    """Print the Task thread: brief, questions, messages, gates, plans, outputs."""
    view = _cli._run_async(_thread(task_id, project_id=project_id))
    if view is None:
        raise click.ClickException(f"Task {task_id} not found")
    for entry in view.entries:
        click.echo(f"#{entry.sequence} {entry.kind} {entry.author}: {entry.text}")


@task_group.command("decisions")
@click.pass_obj
def task_decisions(project_id: str) -> None:
    """List everything in this project that is waiting on a human, soonest due first."""
    items = _cli._run_async(_decisions(project_id))
    if not items:
        click.echo("No open decisions.")
        return
    for item in items:
        click.echo(f"{item.urgency} {item.kind} {item.attention_id}: {item.summary}")


@task_group.command("templates")
@click.pass_obj
def task_templates(_project_id: str) -> None:
    """List the Task templates intake can route to."""
    for template in CATALOGUE.values():
        click.echo(f"{template.id} {template.version}: {template.title}")


@task_group.command("say")
@click.argument("task_id")
@click.argument("text")
@click.pass_obj
def task_say(project_id: str, task_id: str, text: str) -> None:
    """Append one message to TASK_ID's thread."""
    _echo_record(_run_task(_say(task_id, project_id=project_id, text=text)))


@task_group.command("answer")
@click.argument("task_id")
@click.argument("question_id")
@click.argument("answer", required=False)
@click.option("--attention-version", default=1, show_default=True, type=click.IntRange(1))
@click.option(
    "--use-default", is_flag=True, default=False, help="Apply the question's declared default."
)
@click.pass_obj
def task_answer(
    project_id: str,
    task_id: str,
    question_id: str,
    answer: str | None,
    attention_version: int,
    use_default: bool,
) -> None:
    """Answer one open clarification question on TASK_ID."""
    if (answer is None) != use_default:
        raise click.BadParameter("pass an answer argument or --use-default, not both")
    _echo_record(
        _run_task(
            _answer(
                task_id,
                project_id=project_id,
                question_id=question_id,
                answer=None if use_default else answer,
                attention_version=attention_version,
            )
        )
    )


@task_group.command("approve")
@click.argument("task_id")
@click.argument("gate_id")
@click.option("--deny", is_flag=True, default=False, help="Refuse the gate and block the Task.")
@click.option("--note", default=None, help="Why, recorded on the thread.")
@click.pass_obj
def task_approve(project_id: str, task_id: str, gate_id: str, deny: bool, note: str | None) -> None:
    """Decide one gate the Task itself opened; a Work gate is decided with sagewai work."""
    _echo_record(
        _run_task(
            _decide(
                task_id,
                project_id=project_id,
                gate_id=gate_id,
                decision="deny" if deny else "allow",
                note=note,
            )
        )
    )


@task_group.command("pause")
@click.argument("task_id")
@click.pass_obj
def task_pause(project_id: str, task_id: str) -> None:
    """Hold TASK_ID where it stands."""
    _echo_record(_run_task(_pause(task_id, project_id=project_id)))


@task_group.command("resume")
@click.argument("task_id")
@click.pass_obj
def task_resume(project_id: str, task_id: str) -> None:
    """Return a paused TASK_ID to the status the pause interrupted."""
    _echo_record(_run_task(_resume(task_id, project_id=project_id)))


@task_group.command("cancel")
@click.argument("task_id")
@click.option("--note", default=None, help="Why, recorded on the thread.")
@click.pass_obj
def task_cancel(project_id: str, task_id: str, note: str | None) -> None:
    """Stop TASK_ID for good."""
    _echo_record(_run_task(_cancel(task_id, project_id=project_id, note=note)))


@task_group.command("intake")
@click.argument("brief", required=False)
@click.option(
    "--file",
    "brief_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read the brief from a file instead of an argument.",
)
@click.pass_obj
def task_intake(project_id: str, brief: str | None, brief_file: Path | None) -> None:
    """Preview what creating this brief would produce, without writing anything."""
    if (brief is None) == (brief_file is None):
        raise click.BadParameter("pass a brief argument or --file, not both")
    text = brief if brief is not None else brief_file.read_text(encoding="utf-8")
    result = _cli._run_async(_preview(text, project_id=project_id))
    click.echo(json.dumps(result.model_dump(mode="json"), sort_keys=True, indent=2))


@task_group.group("triggers")
def task_triggers() -> None:
    """List, add, and remove this project's approved intake triggers."""


@task_triggers.command("list")
@click.pass_obj
def task_triggers_list(project_id: str) -> None:
    for spec in _cli._run_async(_list_triggers(project_id)):
        state = "enabled" if spec.enabled else "disabled"
        click.echo(
            f"{spec.trigger_id} {spec.source} {spec.filter['owner']}/{spec.filter['repo']} "
            f"{spec.filter['label']} -> {spec.template_id} {spec.template_version} ({state})"
        )


@task_triggers.command("add")
@click.option("--trigger-id", required=True)
@click.option("--owner", required=True)
@click.option("--repo", required=True)
@click.option("--label", required=True)
@click.option("--template-id", required=True)
@click.option("--template-version", default="1", show_default=True)
@click.pass_obj
def task_triggers_add(
    project_id: str,
    trigger_id: str,
    owner: str,
    repo: str,
    label: str,
    template_id: str,
    template_version: str,
) -> None:
    try:
        spec = TaskTriggerSpec(
            trigger_id=trigger_id,
            project_id=project_id,
            source="github_label",
            filter={"owner": owner, "repo": repo, "label": label},
            template_id=template_id,
            template_version=template_version,
        )
    except ValidationError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(_run_task(_put_trigger(spec)))


@task_triggers.command("remove")
@click.argument("trigger_id")
@click.pass_obj
def task_triggers_remove(project_id: str, trigger_id: str) -> None:
    if not _cli._run_async(_delete_trigger(trigger_id, project_id=project_id)):
        raise click.ClickException(f"Trigger {trigger_id} not found")
    click.echo(trigger_id)


def _echo_record(record: TaskRecord) -> None:
    click.echo(f"Task {record.task_id}: {record.status.value}")


def _run_task(coro):
    """Run one write and turn the Task layer's refusals into CLI errors."""
    try:
        return _cli._run_async(coro)
    except TaskNotFoundError as exc:
        raise click.ClickException(f"Task {exc.args[0]} not found") from None
    except (TaskDecisionError, IllegalTransitionError, StaleTaskError) as exc:
        raise click.ClickException(str(exc)) from None


async def _stores() -> tuple[TaskStore, WorkStore, WorkActivityStore]:
    await factory.ensure_schema()
    engine = factory.get_engine()
    return TaskStore(engine=engine), WorkStore(engine=engine), WorkActivityStore(engine=engine)


async def _list(
    project_id: str,
    *,
    statuses: tuple[TaskStatus, ...] | None = None,
    kinds: tuple[TaskKind, ...] | None = None,
    origins: tuple[TaskOrigin, ...] | None = None,
    board_columns: tuple[BoardColumn, ...] | None = None,
    limit: int | None = None,
    order_by: Literal["created_at", "updated_at"] = "created_at",
    descending: bool = False,
) -> list[TaskRecord]:
    task_store, _work_store, _activity = await _stores()
    return await task_store.list_records(
        project_id=project_id,
        statuses=statuses,
        kinds=kinds,
        origins=origins,
        board_columns=board_columns,
        limit=limit,
        order_by=order_by,
        descending=descending,
    )


async def _load_record(task_id: str, *, project_id: str) -> TaskRecord | None:
    task_store, _work_store, _activity = await _stores()
    return await task_store.load_record(task_id, project_id=project_id)


async def _thread(task_id: str, *, project_id: str) -> ThreadView | None:
    task_store, _work_store, _activity = await _stores()
    if await task_store.load_record(task_id, project_id=project_id) is None:
        return None
    return thread_from_events(await task_store.read_events(task_id, project_id=project_id))


async def _decisions(project_id: str) -> tuple[DecisionItem, ...]:
    task_store, work_store, _activity = await _stores()
    return await decision_inbox(
        task_store=task_store,
        work_store=work_store,
        project_id=project_id,
        now=datetime.now(timezone.utc),
    )


async def _create(brief: str, *, project_id: str) -> str:
    task_store, _work_store, _activity = await _stores()
    service = TaskService(store=task_store, artifact_store=LocalArtifactStore())
    task, _record = await service.create(
        brief, project_id=project_id, origin=TaskOrigin.HUMAN, created_by="cli"
    )
    return task.id


async def _service() -> TaskService:
    task_store, _work_store, _activity = await _stores()
    return TaskService(store=task_store, artifact_store=LocalArtifactStore())


async def _say(task_id: str, *, project_id: str, text: str) -> TaskRecord:
    service = await _service()
    return await service.add_message(task_id, project_id=project_id, text=text, actor_ref="cli")


async def _answer(
    task_id: str,
    *,
    project_id: str,
    question_id: str,
    answer: str | None,
    attention_version: int,
) -> TaskRecord:
    service = await _service()
    return await service.answer_clarification(
        task_id,
        project_id=project_id,
        question_id=question_id,
        attention_version=attention_version,
        answer=answer,
        actor_ref="cli",
    )


async def _decide(
    task_id: str,
    *,
    project_id: str,
    gate_id: str,
    decision: Literal["allow", "deny"],
    note: str | None,
) -> TaskRecord:
    service = await _service()
    return await service.decide_gate(
        task_id,
        project_id=project_id,
        gate_id=gate_id,
        decision=decision,
        actor_ref="cli",
        note=note,
    )


async def _pause(task_id: str, *, project_id: str) -> TaskRecord:
    service = await _service()
    return await service.pause(task_id, project_id=project_id, actor_ref="cli")


async def _resume(task_id: str, *, project_id: str) -> TaskRecord:
    service = await _service()
    return await service.resume(task_id, project_id=project_id, actor_ref="cli")


async def _cancel(task_id: str, *, project_id: str, note: str | None) -> TaskRecord:
    service = await _service()
    return await service.cancel(task_id, project_id=project_id, actor_ref="cli", note=note)


async def _preview(brief: str, *, project_id: str) -> IntakeResult:
    task_store, _work_store, _activity = await _stores()
    return intake_route(brief, await task_store.get_defaults(project_id=project_id))


async def _list_triggers(project_id: str) -> list[TaskTriggerSpec]:
    task_store, _work_store, _activity = await _stores()
    return await task_store.list_triggers(project_id=project_id, enabled_only=False)


async def _put_trigger(spec: TaskTriggerSpec) -> str:
    task_store, _work_store, _activity = await _stores()
    await task_store.put_trigger(spec)
    return spec.trigger_id


async def _delete_trigger(trigger_id: str, *, project_id: str) -> bool:
    task_store, _work_store, _activity = await _stores()
    return await task_store.delete_trigger(trigger_id, project_id=project_id)


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
