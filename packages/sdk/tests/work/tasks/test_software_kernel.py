# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""task_id, evidence, and create_issue reach the software kernel."""

from __future__ import annotations

import pytest

from sagewai.work.events import WorkEventType
from sagewai.work.profiles.software.github import GitHubIssue, GitHubIssueLifecycle
from sagewai.work.profiles.software.models import SoftwareContractContext, SoftwareRepositoryOutcome
from sagewai.work.store import WorkStore
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.test_github import FakeBranchPublisher, FakeGitHub, FakeSoftwareLifecycle

ISSUE_URL = "https://github.com/octocat/hello-world/issues/42"


def _contract_event(events):
    return next(event for event in events if event.event_type is WorkEventType.CONTRACT_ACCEPTED)


class RecordingGitHub(FakeGitHub):
    """FakeGitHub plus the create_issue operation the coordinator needs."""

    def __init__(self) -> None:
        super().__init__()
        self.created: list[dict] = []
        self.labeled_issues: tuple[GitHubIssue, ...] = ()

    async def list_labeled_issues(self, *, owner, repo, label):
        return self.labeled_issues

    async def create_issue(self, *, owner, repo, title, body, labels):
        self.created.append(
            {"owner": owner, "repo": repo, "title": title, "body": body, "labels": labels}
        )
        issue = GitHubIssue(
            project_id="project-a",
            owner=owner,
            repo=repo,
            number=len(self.created),
            url=f"https://github.com/{owner}/{repo}/issues/{len(self.created)}",
            title=title,
            body=body,
            default_branch="main",
        )
        self.labeled_issues = (*self.labeled_issues, issue)
        return issue


def _flow(store: WorkStore, **kwargs) -> GitHubIssueLifecycle:
    return GitHubIssueLifecycle(
        work_store=store,
        software_lifecycle=FakeSoftwareLifecycle(store),
        github=FakeGitHub(),
        branch_publisher=FakeBranchPublisher(),
        repository_outcome=SoftwareRepositoryOutcome.MERGED,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_task_id_reaches_the_contract_profile_context(dialect_engine) -> None:  # noqa: F811
    store = WorkStore(engine=dialect_engine)
    await store.init()
    record = await _flow(store, task_id="task-1").start(
        issue_url=ISSUE_URL, project_id="project-a", base_sha="a" * 40
    )
    events = await store.read_events(record.work_id, project_id="project-a")
    proposed = _contract_event(events)
    context = SoftwareContractContext.model_validate(proposed.payload_json["profile_context"])
    assert context.task_id == "task-1"


@pytest.mark.asyncio
async def test_no_task_id_leaves_the_context_unchanged(dialect_engine) -> None:  # noqa: F811
    store = WorkStore(engine=dialect_engine)
    await store.init()
    record = await _flow(store).start(
        issue_url=ISSUE_URL, project_id="project-a", base_sha="a" * 40
    )
    events = await store.read_events(record.work_id, project_id="project-a")
    proposed = _contract_event(events)
    assert SoftwareContractContext.model_validate(
        proposed.payload_json["profile_context"]
    ).task_id is None
    assert tuple(proposed.payload_json["evidence_refs"]) == (ISSUE_URL,)


@pytest.mark.asyncio
async def test_start_merges_extra_evidence_after_the_issue_url(
    dialect_engine,  # noqa: F811
) -> None:
    store = WorkStore(engine=dialect_engine)
    await store.init()
    record = await _flow(store).start(
        issue_url=ISSUE_URL,
        project_id="project-a",
        base_sha="a" * 40,
        evidence_refs=("https://github.com/octocat/hello-world/pull/7",),
    )
    events = await store.read_events(record.work_id, project_id="project-a")
    proposed = _contract_event(events)
    assert tuple(proposed.payload_json["evidence_refs"]) == (
        ISSUE_URL,
        "https://github.com/octocat/hello-world/pull/7",
    )


@pytest.mark.asyncio
async def test_the_catalog_client_creates_a_labelled_issue() -> None:
    from sagewai.work.profiles.software.github import CatalogGitHubClient

    calls: list[dict] = []

    async def github_callable(payload: dict) -> dict:
        calls.append(payload)
        if payload["_operation"] == "get_repo":
            return {"default_branch": "main"}
        return {"number": 11, "html_url": "https://github.com/o/r/issues/11"}

    client = CatalogGitHubClient(project_id="project-a", github_callable=github_callable)
    issue = await client.create_issue(
        owner="o", repo="r", title="Add the retry queue", body="body", labels=("sagewai-task:t1",)
    )
    assert issue.url == "https://github.com/o/r/issues/11"
    assert issue.number == 11
    assert issue.default_branch == "main"
    assert calls[1] == {
        "_operation": "create_issue",
        "owner": "o",
        "repo": "r",
        "title": "Add the retry queue",
        "body": "body",
        "labels": ["sagewai-task:t1"],
    }


def test_the_catalog_declares_labels_on_create_issue() -> None:
    from sagewai.tools import registry

    registry._reset()
    registry.load()
    entry = registry.lookup("github")
    operation = entry.exec_["http"]["operations"]["create_issue"]
    assert operation["input_schema"]["properties"]["labels"] == {
        "type": "array",
        "items": {"type": "string"},
    }
