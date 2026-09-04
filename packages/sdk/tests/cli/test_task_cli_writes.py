# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""sagewai task say, answer, approve, lifecycle, intake, and triggers."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

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

BRIEF = (
    "Implement the retry queue in the payments service repository, add the failing test first, "
    "and open a pull request when the deterministic verification command passes."
)


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
async def clarifying(wired, dialect_engine):  # noqa: F811
    """A Task intake could not route, so it asked; ``q1`` is its first open question."""
    store = TaskStore(engine=dialect_engine)
    await store.init()
    await _create(
        store,
        "t-4",
        extra=(
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        {
                            "id": "q1",
                            "text": "Which queue?",
                            "kind": "text",
                            "options": [],
                            "default": None,
                            "defaultable": False,
                            "rationale": "",
                            "attention_version": 1,
                        }
                    ],
                    "deadline_at": NOW.isoformat(),
                },
            ),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": "CLARIFYING"}),
        ),
    )
    return wired


@pytest.fixture
async def defaultable(wired, dialect_engine):  # noqa: F811
    """A Task with one defaultable clarification question."""
    store = TaskStore(engine=dialect_engine)
    await store.init()
    await _create(
        store,
        "t-7",
        extra=(
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        {
                            "id": "q1",
                            "text": "Which queue?",
                            "kind": "text",
                            "options": [],
                            "default": "redis",
                            "defaultable": True,
                            "rationale": "",
                            "attention_version": 1,
                        }
                    ],
                    "deadline_at": NOW.isoformat(),
                },
            ),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": "CLARIFYING"}),
        ),
    )
    return wired


@pytest.fixture
async def gated(wired, dialect_engine):  # noqa: F811
    """A Task holding its own plan gate."""
    store = TaskStore(engine=dialect_engine)
    await store.init()
    await _create(
        store,
        "t-5",
        extra=((TaskEventType.GATE_REQUESTED, {"gate_id": "plan:t-5:1", "question": "Approve."}),),
    )
    return wired


@pytest.fixture
async def gated_work(wired, dialect_engine):  # noqa: F811
    """A Task mirroring a Work's merge gate, which sagewai task must refuse to decide."""
    store = TaskStore(engine=dialect_engine)
    await store.init()
    await _create(
        store,
        "t-6",
        extra=(
            (
                TaskEventType.GATE_REQUESTED,
                {"gate_id": "merge:w1:3", "question": "Approve merge.", "work_id": "w1"},
            ),
        ),
    )
    return wired


def test_say_appends_a_message_to_the_thread(wired, seeded, dialect_engine) -> None:  # noqa: F811
    result = wired.invoke(task_group, ["--project", "project-a", "say", "t-1", "use redis"])
    thread = wired.invoke(task_group, ["--project", "project-a", "thread", "t-1"])
    store = TaskStore(engine=dialect_engine)
    events = asyncio.run(store.read_events("t-1", project_id="project-a"))

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "Task t-1: PLANNING"
    assert "message human: use redis" in thread.output
    assert events[-1].actor_ref == "cli"


def test_say_of_an_unknown_task_names_the_task(wired, seeded) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "say", "t-9", "use redis"])

    assert result.exit_code == 1
    assert "Task t-9 not found" in result.output


def test_answer_binds_to_the_attention_version(wired, clarifying) -> None:
    stale = wired.invoke(
        task_group,
        ["--project", "project-a", "answer", "t-4", "q1", "redis", "--attention-version", "7"],
    )
    current = wired.invoke(task_group, ["--project", "project-a", "answer", "t-4", "q1", "redis"])

    assert stale.exit_code == 1
    assert "attention version" in stale.output
    assert current.exit_code == 0, current.output


def test_answer_can_use_the_declared_default(wired, defaultable, dialect_engine) -> None:  # noqa: F811
    result = wired.invoke(
        task_group, ["--project", "project-a", "answer", "t-7", "q1", "--use-default"]
    )
    store = TaskStore(engine=dialect_engine)
    events = asyncio.run(store.read_events("t-7", project_id="project-a"))

    assert result.exit_code == 0, result.output
    assert events[-2].event_type is TaskEventType.CLARIFICATION_DEFAULTED


@pytest.mark.parametrize(
    "args",
    [
        ["answer", "t-4", "q1"],
        ["answer", "t-4", "q1", "redis", "--use-default"],
    ],
)
def test_answer_requires_exactly_one_answer_source(wired, clarifying, args) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", *args])

    assert result.exit_code == 2
    assert "pass an answer argument or --use-default, not both" in result.output


def test_approve_decides_a_task_gate(wired, gated) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "approve", "t-5", "plan:t-5:1"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "Task t-5: PLANNING"


def test_approve_deny_blocks_the_task(wired, gated) -> None:
    result = wired.invoke(
        task_group,
        [
            "--project",
            "project-a",
            "approve",
            "t-5",
            "plan:t-5:1",
            "--deny",
            "--note",
            "wrong repo",
        ],
    )
    thread = wired.invoke(task_group, ["--project", "project-a", "thread", "t-5"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "Task t-5: BLOCKED"
    assert "message human: wrong repo" in thread.output


def test_approve_of_a_work_gate_points_at_sagewai_work(wired, gated_work) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "approve", "t-6", "merge:w1:3"])

    assert result.exit_code == 1
    assert "sagewai work approve" in result.output


def test_pause_resume_cancel_move_the_status(wired, seeded, dialect_engine) -> None:  # noqa: F811
    paused = wired.invoke(task_group, ["--project", "project-a", "pause", "t-1"])
    resumed = wired.invoke(task_group, ["--project", "project-a", "resume", "t-1"])
    cancelled = wired.invoke(
        task_group, ["--project", "project-a", "cancel", "t-1", "--note", "done by hand"]
    )
    thread = wired.invoke(task_group, ["--project", "project-a", "thread", "t-1"])

    store = TaskStore(engine=dialect_engine)
    events = asyncio.run(store.read_events("t-1", project_id="project-a"))

    assert paused.output.strip() == "Task t-1: PAUSED"
    assert resumed.output.strip() == "Task t-1: PLANNING"
    assert cancelled.output.strip() == "Task t-1: CANCELLED"
    assert "message human: done by hand" in thread.output
    assert events[-1].actor_ref == "cli"


def test_resuming_a_running_task_fails(wired, seeded) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "resume", "t-1"])

    assert result.exit_code == 1
    assert "not PAUSED" in result.output


def test_intake_prints_the_preview_as_json(wired) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "intake", BRIEF])

    assert result.exit_code == 0, result.output
    preview = json.loads(result.output)
    assert preview["template_id"] == "software_delivery"
    assert "preview" in preview


def test_intake_reads_a_brief_file(wired, tmp_path) -> None:
    path = tmp_path / "brief.md"
    path.write_text(BRIEF, encoding="utf-8")
    inline = wired.invoke(task_group, ["--project", "project-a", "intake", BRIEF])
    from_file = wired.invoke(task_group, ["--project", "project-a", "intake", "--file", str(path)])

    assert inline.exit_code == 0, inline.output
    assert from_file.exit_code == 0, from_file.output
    assert json.loads(from_file.output) == json.loads(inline.output)


def test_triggers_add_list_and_remove(wired) -> None:
    added = wired.invoke(
        task_group,
        [
            "--project",
            "project-a",
            "triggers",
            "add",
            "--trigger-id",
            "tr-1",
            "--owner",
            "o",
            "--repo",
            "r",
            "--label",
            "sagewai",
            "--template-id",
            "software_delivery",
        ],
    )
    listed = wired.invoke(task_group, ["--project", "project-a", "triggers", "list"])
    removed = wired.invoke(task_group, ["--project", "project-a", "triggers", "remove", "tr-1"])
    missing = wired.invoke(task_group, ["--project", "project-a", "triggers", "remove", "tr-1"])

    assert added.exit_code == 0, added.output
    assert listed.output.strip() == "tr-1 github_label o/r sagewai -> software_delivery 1 (enabled)"
    assert removed.exit_code == 0
    assert missing.exit_code == 1


def test_triggers_add_reports_invalid_values_without_traceback(wired) -> None:
    result = wired.invoke(
        task_group,
        [
            "--project",
            "project-a",
            "triggers",
            "add",
            "--trigger-id",
            "",
            "--owner",
            "o",
            "--repo",
            "r",
            "--label",
            "sagewai",
            "--template-id",
            "software_delivery",
        ],
    )

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output
