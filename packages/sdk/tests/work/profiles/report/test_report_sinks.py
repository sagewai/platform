# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Where a composed report is delivered, and what its post-check sees (section 12 step 4)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.work.models import Reversibility
from sagewai.work.profiles.report.models import ReportArchive, ReportClaim, SourceSnapshot
from sagewai.work.profiles.report.sinks import (
    COMMENT_LIMIT_BYTES,
    ConsoleSink,
    GitHubIssueSink,
    comment_body,
)
from sagewai.work.tasks.models import Sink
from tests.work.tasks.test_software_kernel import RecordingGitHub

PROJECT = "project-a"
NOW = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
BODY = "# Summary\n\nVendor A shipped a queue.\n"
ISSUE_URL = "https://github.com/octocat/hello-world/issues/42"


def _archive(report_ref: str, body: str = BODY) -> ReportArchive:
    encoded = body.encode("utf-8")
    return ReportArchive(
        report_ref=report_ref,
        report_bytes=len(encoded),
        report_sha256=hashlib.sha256(encoded).hexdigest(),
        snapshots=(
            SourceSnapshot(
                snapshot_ref="artifact://sha256:" + "b" * 64,
                url="https://a.example/blog",
                fetched_at=NOW,
                content_sha256="b" * 64,
                size_bytes=12,
            ),
        ),
        claims=(
            ReportClaim(
                statement="Vendor A shipped a queue.",
                snapshot_refs=("artifact://sha256:" + "b" * 64,),
            ),
        ),
    )


def _console_sink() -> Sink:
    return Sink(kind="console", version=1)


def _issue_sink(version: int = 2) -> Sink:
    return Sink(kind="github_issue", version=version, issue_url=ISSUE_URL)


@pytest.mark.asyncio
async def test_the_console_sink_reads_its_artifact_back(tmp_path) -> None:
    artifacts = LocalArtifactStore(root=tmp_path)
    stored = artifacts.put_bytes(
        BODY.encode("utf-8"),
        project_id=PROJECT,
        media_type="text/markdown",
        created_by="work:w1",
    )
    sink = ConsoleSink(artifact_store=artifacts)

    delivery = await sink.deliver(
        project_id=PROJECT, sink=_console_sink(), body=BODY, archive=_archive(stored.storage_ref)
    )

    assert delivery.external_ref == stored.storage_ref
    assert delivery.evidence_refs == (stored.storage_ref,)
    assert delivery.observation == {
        "check": "artifact_read_back",
        "passed": True,
        "detail": f"{len(BODY.encode('utf-8'))} bytes read back at {stored.storage_ref}",
        "evidence_refs": [stored.storage_ref],
    }


@pytest.mark.asyncio
async def test_a_console_delivery_whose_digest_moved_fails_its_post_check(tmp_path) -> None:
    artifacts = LocalArtifactStore(root=tmp_path)
    stored = artifacts.put_bytes(
        b"something else",
        project_id=PROJECT,
        media_type="text/markdown",
        created_by="work:w1",
    )
    sink = ConsoleSink(artifact_store=artifacts)

    delivery = await sink.deliver(
        project_id=PROJECT, sink=_console_sink(), body=BODY, archive=_archive(stored.storage_ref)
    )

    assert delivery.observation["passed"] is False


@pytest.mark.asyncio
async def test_a_small_report_posts_verbatim_and_reads_its_body_back() -> None:
    github = RecordingGitHub()
    sink = GitHubIssueSink(github=github)

    delivery = await sink.deliver(
        project_id=PROJECT,
        sink=_issue_sink(),
        body=BODY,
        archive=_archive("artifact://sha256:" + "a" * 64),
    )

    assert github.comments == [(ISSUE_URL, BODY)]
    assert delivery.observation["check"] == "comment_read_back"
    assert delivery.observation["passed"] is True
    assert delivery.external_ref.endswith("#issuecomment-1")
    assert delivery.evidence_refs == (delivery.external_ref, "artifact://sha256:" + "a" * 64)


@pytest.mark.asyncio
async def test_a_read_back_body_differing_only_in_line_endings_still_passes() -> None:
    class _CarriageReturns(RecordingGitHub):
        async def comment_issue(self, issue_url, body):
            comment = await super().comment_issue(issue_url, body)
            return comment.model_copy(update={"body": body.replace("\n", "\r\n")})

    delivery = await GitHubIssueSink(github=_CarriageReturns()).deliver(
        project_id=PROJECT,
        sink=_issue_sink(),
        body=BODY,
        archive=_archive("artifact://sha256:" + "a" * 64),
    )

    assert delivery.observation["passed"] is True


@pytest.mark.asyncio
async def test_a_large_report_posts_the_truncated_comment_and_reads_it_back() -> None:
    github = RecordingGitHub()
    body = "# Summary\n\n" + ("x" * 70_000)
    archive = _archive("artifact://sha256:" + "a" * 64, body=body)

    delivery = await GitHubIssueSink(github=github).deliver(
        project_id=PROJECT,
        sink=_issue_sink(),
        body=body,
        archive=archive,
    )

    assert github.comments[0][1] == comment_body(body, archive)
    assert delivery.observation["passed"] is True


@pytest.mark.asyncio
async def test_a_read_back_body_that_changed_fails() -> None:
    class _ChangedBody(RecordingGitHub):
        async def comment_issue(self, issue_url, body):
            comment = await super().comment_issue(issue_url, body)
            return comment.model_copy(update={"body": "something else"})

    delivery = await GitHubIssueSink(github=_ChangedBody()).deliver(
        project_id=PROJECT,
        sink=_issue_sink(),
        body=BODY,
        archive=_archive("artifact://sha256:" + "a" * 64),
    )

    assert delivery.observation["passed"] is False


def test_a_report_over_the_comment_limit_posts_a_summary_and_the_digest() -> None:
    body = "# Summary\n\n" + ("x" * 70_000)
    archive = _archive("artifact://sha256:" + "a" * 64, body=body)

    posted = comment_body(body, archive)

    assert len(posted.encode("utf-8")) <= COMMENT_LIMIT_BYTES
    assert posted.startswith("# Summary")
    assert archive.report_ref in posted
    assert archive.report_sha256 in posted


def test_each_sink_declares_its_own_reversibility_and_rollback() -> None:
    archive = _archive("artifact://sha256:" + "a" * 64)
    assert ConsoleSink.kind == "console"
    assert GitHubIssueSink.kind == "github_issue"

    console = ConsoleSink(artifact_store=None).action(
        project_id=PROJECT, work_id="w1", sink=_console_sink(), archive=archive
    )
    issue = GitHubIssueSink(github=None).action(
        project_id=PROJECT, work_id="w1", sink=_issue_sink(), archive=archive
    )

    assert (console.reversibility, console.rollback) == (
        Reversibility.SNAPSHOT_REVERSIBLE,
        None,
    )
    assert console.risk == "low"
    assert console.scope == archive.report_ref
    assert console.post_check == "artifact_read_back"
    assert (issue.reversibility, issue.rollback) == (
        Reversibility.COMPENSATABLE,
        "delete_comment",
    )
    assert issue.risk == "medium"
    assert issue.scope == ISSUE_URL  # what a human is asked to approve
    assert issue.post_check == "comment_read_back"
    assert issue.evidence_refs == (archive.report_ref, ISSUE_URL)
