# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Admin-approved triggers turn labelled issues into Tasks of origin trigger, once each."""

from __future__ import annotations

import pytest

from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.work.profiles.software.github import GitHubIssue
from sagewai.work.store import WorkStore
from sagewai.work.tasks.models import (
    Authority,
    GateMode,
    TaskDefaults,
    TaskOrigin,
    TaskTriggerSpec,
)
from sagewai.work.tasks.service import TaskCreationError, TaskService
from sagewai.work.tasks.store import TaskStore
from sagewai.work.tasks.triggers import TriggerIntake
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import NOW, _task

PROJECT = "project-a"
SPEC = TaskTriggerSpec(
    trigger_id="t1",
    project_id=PROJECT,
    source="github_label",
    filter={"owner": "octocat", "repo": "hello-world", "label": "sagewai"},
    template_id="software_delivery",
    template_version="1",
    authority=Authority(merge=GateMode.REQUIRE),
)
BRIEF = (
    "Implement the retry queue in the payments service repository, add the failing test first, "
    "and open a pull request when the deterministic verification command passes."
)


class _Issues:
    def __init__(self, *issues: GitHubIssue) -> None:
        self.issues = issues
        self.calls: list[tuple[str, str, str]] = []

    async def list_labeled_issues(self, *, owner, repo, label):
        self.calls.append((owner, repo, label))
        return self.issues


def _issue(number: int) -> GitHubIssue:
    return GitHubIssue(
        project_id=PROJECT,
        owner="octocat",
        repo="hello-world",
        number=number,
        url=f"https://github.com/octocat/hello-world/issues/{number}",
        title="Retry queue",
        body=BRIEF,
        default_branch="main",
    )


@pytest.fixture
async def intake(dialect_engine, tmp_path):  # noqa: F811
    task_store = TaskStore(engine=dialect_engine)
    work_store = WorkStore(engine=dialect_engine)
    await task_store.init()
    await work_store.init()
    await task_store.put_defaults(
        TaskDefaults(project_id=PROJECT, target=_task().target), expected_revision=0
    )
    await task_store.put_trigger(SPEC)
    github = _Issues(_issue(1), _issue(2))
    service = TaskService(
        store=task_store, artifact_store=LocalArtifactStore(root=tmp_path / "objects")
    )
    return (
        TriggerIntake(
            task_store=task_store,
            work_store=work_store,
            service=service,
            github_factory=lambda _spec: github,
        ),
        task_store,
        github,
    )


@pytest.mark.asyncio
async def test_the_trigger_round_trips_through_the_store(intake) -> None:
    _intake, task_store, _github = intake
    stored = await task_store.list_triggers(project_id=PROJECT)
    assert stored == [SPEC]
    assert await task_store.list_triggers(project_id="project-b") == []
    assert await task_store.delete_trigger("t1", project_id=PROJECT) is True
    assert await task_store.list_triggers(project_id=PROJECT) == []


@pytest.mark.asyncio
async def test_each_labelled_issue_becomes_one_task_bounded_by_trigger_authority(
    intake,
) -> None:
    trigger_intake, task_store, github = intake
    created = await trigger_intake.run(project_id=PROJECT, now=NOW)
    assert len(created) == 2
    assert github.calls == [("octocat", "hello-world", "sagewai")]
    task, _record = await task_store.load(created[0], project_id=PROJECT)
    assert task.origin is TaskOrigin.TRIGGER
    assert task.origin_ref == "t1"
    assert task.source_ref.endswith("/issues/1")
    assert task.authority.merge is GateMode.REQUIRE
    assert task.routing.prefer_free_implementation is False


@pytest.mark.asyncio
async def test_a_second_run_creates_nothing(intake) -> None:
    trigger_intake, task_store, _github = intake
    await trigger_intake.run(project_id=PROJECT, now=NOW)
    assert await trigger_intake.run(project_id=PROJECT, now=NOW) == []
    assert len(await task_store.list_records(project_id=PROJECT)) == 2


@pytest.mark.asyncio
async def test_a_disabled_trigger_is_skipped(intake) -> None:
    trigger_intake, task_store, _github = intake
    await task_store.put_trigger(SPEC.model_copy(update={"enabled": False}))
    assert await trigger_intake.run(project_id=PROJECT, now=NOW) == []


@pytest.mark.asyncio
async def test_a_failed_task_create_does_not_consume_the_trigger_receipt(
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    task_store = TaskStore(engine=dialect_engine)
    work_store = WorkStore(engine=dialect_engine)
    await task_store.init()
    await work_store.init()
    await task_store.put_defaults(TaskDefaults(project_id=PROJECT), expected_revision=0)
    await task_store.put_trigger(SPEC)
    github = _Issues(_issue(7))
    service = TaskService(
        store=task_store, artifact_store=LocalArtifactStore(root=tmp_path / "objects")
    )
    trigger_intake = TriggerIntake(
        task_store=task_store,
        work_store=work_store,
        service=service,
        github_factory=lambda _spec: github,
    )

    with pytest.raises(TaskCreationError):
        await trigger_intake.run(project_id=PROJECT, now=NOW)

    defaults = await task_store.get_defaults(project_id=PROJECT)
    await task_store.put_defaults(
        TaskDefaults(project_id=PROJECT, target=_task().target),
        expected_revision=defaults.revision,
    )
    created = await trigger_intake.run(project_id=PROJECT, now=NOW)

    assert len(created) == 1
    task, _record = await task_store.load(created[0], project_id=PROJECT)
    assert task.source_ref.endswith("/issues/7")


def test_a_github_label_trigger_must_filter_on_owner_repo_and_label() -> None:
    with pytest.raises(ValueError):
        SPEC.model_copy(update={"filter": {"label": "sagewai"}}).model_validate(
            SPEC.model_dump() | {"filter": {"label": "sagewai"}}
        )
