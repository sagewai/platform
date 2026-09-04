# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""sagewai task list, board, status, thread, decisions and templates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from sagewai.cli.tasks import task_group
from sagewai.work.tasks.events import TaskEvent, TaskEventType, fold_record
from sagewai.work.tasks.models import TaskDefaults, TaskStatus
from sagewai.work.tasks.store import TaskStore
from sagewai.work.tasks.writer import TaskWriter, status_entry
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _record, _task

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def wired(dialect_engine, monkeypatch, tmp_path):  # noqa: F811
    from sagewai.cli import tasks as tasks_module

    seeded_defaults = False

    async def _ensure_schema() -> None:
        nonlocal seeded_defaults
        store = TaskStore(engine=dialect_engine)
        await store.init()
        if seeded_defaults:
            return
        await store.put_defaults(
            TaskDefaults(project_id="project-a", target=_task().target), expected_revision=0
        )
        seeded_defaults = True

    monkeypatch.setattr(tasks_module.factory, "ensure_schema", _ensure_schema)
    monkeypatch.setattr(tasks_module.factory, "get_engine", lambda: dialect_engine)
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    return CliRunner()


def _event(task, sequence: int, event_type: TaskEventType, payload: dict) -> TaskEvent:
    return TaskEvent(
        id=f"{task.id}-{sequence}",
        project_id=task.project_id,
        task_id=task.id,
        sequence=sequence,
        event_type=event_type,
        actor_type="system",
        actor_ref="coordinator",
        payload_json=payload,
        created_at=NOW,
    )


async def _create(store: TaskStore, task_id: str, extra: tuple = ()) -> None:
    task = _task(task_id, project_id="project-a")
    events = (
        _event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),
        *(
            _event(task, index, kind, payload)
            for index, (kind, payload) in enumerate(extra, start=2)
        ),
    )
    await store.create(task, events=events, record=fold_record(_record(task), events))


@pytest.fixture
async def seeded(wired, dialect_engine):  # noqa: F811
    """Two Tasks: one in the inbox, one blocked and therefore in the decisions inbox."""
    store = TaskStore(engine=dialect_engine)
    await store.init()
    await _create(store, "t-1")
    await _create(store, "t-2")
    record = await store.load_record("t-2", project_id="project-a")
    await TaskWriter(store).append(record, [status_entry(record, TaskStatus.BLOCKED)], now=NOW)
    return wired


@pytest.fixture
async def seeded_thread(wired, dialect_engine):  # noqa: F811
    store = TaskStore(engine=dialect_engine)
    await store.init()
    await _create(
        store,
        "t-3",
        extra=(
            (
                TaskEventType.BRIEF_RECORDED,
                {"brief_ref": "artifact://sha256:" + "a" * 64, "summary": "Build the thing"},
            ),
            (TaskEventType.TASK_MESSAGE, {"author": "coordinator", "text": "planning", "refs": []}),
        ),
    )
    return wired


@pytest.fixture
async def seeded_touched_board(wired, dialect_engine):  # noqa: F811
    """Two inbox Tasks; the older one was touched last, so the board must print it first."""
    store = TaskStore(engine=dialect_engine)
    await store.init()
    await _create(store, "t-old")
    await _create(store, "t-new")
    record = await store.load_record("t-old", project_id="project-a")
    await TaskWriter(store).append(
        record,
        [(TaskEventType.TASK_MESSAGE, {"author": "human", "text": "later", "refs": []})],
        now=NOW + timedelta(minutes=1),
    )
    return wired


def test_list_prints_one_line_per_task(wired, seeded) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "list"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "t-1 PLANNING inbox: Build the thing",
        "t-2 BLOCKED needs_you: Build the thing",
    ]


def test_list_filters_by_status_and_column(wired, seeded) -> None:
    by_status = wired.invoke(task_group, ["--project", "project-a", "list", "--status", "BLOCKED"])
    by_column = wired.invoke(task_group, ["--project", "project-a", "list", "--column", "inbox"])

    assert [line.split()[0] for line in by_status.output.splitlines()] == ["t-2"]
    assert [line.split()[0] for line in by_column.output.splitlines()] == ["t-1"]


def test_list_filters_by_kind_origin_and_limit(wired, seeded) -> None:
    by_kind = wired.invoke(task_group, ["--project", "project-a", "list", "--kind", "batch"])
    by_trigger = wired.invoke(task_group, ["--project", "project-a", "list", "--origin", "trigger"])
    by_human = wired.invoke(task_group, ["--project", "project-a", "list", "--origin", "human"])
    limited = wired.invoke(task_group, ["--project", "project-a", "list", "--limit", "1"])

    assert [line.split()[0] for line in by_kind.output.splitlines()] == ["t-1", "t-2"]
    assert by_trigger.output == ""
    assert [line.split()[0] for line in by_human.output.splitlines()] == ["t-1", "t-2"]
    assert len(limited.output.splitlines()) == 1


def test_list_rejects_an_unknown_status(wired, seeded) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "list", "--status", "SLEEPING"])

    assert result.exit_code == 2
    assert "SLEEPING" in result.output


def test_board_prints_every_column(wired, seeded) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "board"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "inbox:",
        "  t-1 PLANNING: Build the thing",
        "needs_you:",
        "  t-2 BLOCKED: Build the thing",
        "planned:",
        "in_progress:",
        "done:",
    ]


def test_board_prints_touched_task_first_within_a_column(wired, seeded_touched_board) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "board"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[:3] == [
        "inbox:",
        "  t-old PLANNING: Build the thing",
        "  t-new PLANNING: Build the thing",
    ]


def test_status_prints_the_record_line(wired, seeded) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "status", "t-1"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "Task t-1: PLANNING"


def test_status_of_an_unknown_task_fails(wired, seeded) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "status", "missing"])

    assert result.exit_code == 1
    assert "Task missing not found" in result.output


def test_thread_of_an_unknown_task_fails(wired, seeded) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "thread", "missing"])

    assert result.exit_code == 1
    assert "Task missing not found" in result.output


def test_thread_prints_one_line_per_entry(wired, seeded_thread) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "thread", "t-3"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "#2 brief system: Build the thing",
        "#3 message coordinator: planning",
    ]


def test_decisions_prints_the_inbox_soonest_first(wired, seeded) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "decisions"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "now task blocked: Build the thing"


def test_decisions_on_a_quiet_project_says_so(wired) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "decisions"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "No open decisions."


def test_templates_lists_the_catalogue(wired) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "templates"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "software_delivery 1: Software delivery",
        "scheduled_research_report 2: Scheduled research report",
    ]


def test_every_read_command_refuses_the_global_project(wired) -> None:
    for command in ("list", "board", "decisions", "templates"):
        result = wired.invoke(task_group, ["--project", "global", command])
        assert result.exit_code == 2, command
        assert "Tasks require an explicit project" in result.output
