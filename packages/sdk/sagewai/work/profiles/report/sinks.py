# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Where a composed report is delivered (spec section 12 step 4)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sagewai.artifacts.object_store import ArtifactStore
from sagewai.work.models import ActionRequest
from sagewai.work.profiles.report.models import ReportArchive
from sagewai.work.profiles.software.github import GitHubClient
from sagewai.work.tasks.actions import deliver_action
from sagewai.work.tasks.models import Sink

COMMENT_LIMIT_BYTES = 60_000


@dataclass(frozen=True)
class SinkDelivery:
    """What one sink did, and what its post-check saw."""

    external_ref: str
    evidence_refs: tuple[str, ...]
    observation: dict[str, Any]


def comment_body(body: str, archive: ReportArchive) -> str:
    """Under 60 KB the whole report; over it a summary plus the artifact digest."""
    encoded = body.encode("utf-8")
    if len(encoded) <= COMMENT_LIMIT_BYTES:
        return body
    head = encoded[: COMMENT_LIMIT_BYTES - 512].decode("utf-8", "ignore")
    return (
        f"{head}\n\n---\n"
        f"Truncated at {COMMENT_LIMIT_BYTES} bytes. Full report: {archive.report_ref} "
        f"(sha256 {archive.report_sha256}, {archive.report_bytes} bytes)."
    )


def _same_text(left: str, right: str) -> bool:
    """GitHub rewrites line endings on the comment it persists; nothing else may differ."""
    return left.replace("\r\n", "\n") == right.replace("\r\n", "\n")


class ConsoleSink:
    """Record the redacted artifact on the Work; the console renders it."""

    kind = "console"

    def __init__(self, *, artifact_store: ArtifactStore) -> None:
        self._artifacts = artifact_store

    def action(
        self, *, project_id: str, work_id: str, sink: Sink, archive: ReportArchive
    ) -> ActionRequest:
        return deliver_action(
            project_id,
            work_id=work_id,
            scope=archive.report_ref,
            evidence_refs=(archive.report_ref,),
            rollback=None,
        )

    async def deliver(
        self, *, project_id: str, sink: Sink, body: str, archive: ReportArchive
    ) -> SinkDelivery:
        stored = self._artifacts.read(archive.report_ref, project_id=project_id)
        digest = hashlib.sha256(stored).hexdigest()
        return SinkDelivery(
            external_ref=archive.report_ref,
            evidence_refs=(archive.report_ref,),
            observation={
                "check": "artifact_read_back",
                "passed": digest == archive.report_sha256,
                "detail": f"{len(stored)} bytes read back at {archive.report_ref}",
                "evidence_refs": [archive.report_ref],
            },
        )


class GitHubIssueSink:
    """Post the report as an issue comment behind the deliver gate."""

    kind = "github_issue"

    def __init__(self, *, github: GitHubClient) -> None:
        self._github = github

    def action(
        self, *, project_id: str, work_id: str, sink: Sink, archive: ReportArchive
    ) -> ActionRequest:
        return deliver_action(
            project_id,
            work_id=work_id,
            scope=str(sink.issue_url),
            evidence_refs=(archive.report_ref, str(sink.issue_url)),
            rollback="delete_comment",
        )

    async def deliver(
        self, *, project_id: str, sink: Sink, body: str, archive: ReportArchive
    ) -> SinkDelivery:
        posted = comment_body(body, archive)
        comment = await self._github.comment_issue(str(sink.issue_url), posted)
        return SinkDelivery(
            external_ref=comment.url,
            evidence_refs=(comment.url, archive.report_ref),
            observation={
                "check": "comment_read_back",
                "passed": _same_text(comment.body, posted),
                "detail": f"comment {comment.id} on {sink.issue_url}",
                "evidence_refs": [comment.url],
            },
        )


__all__ = ["COMMENT_LIMIT_BYTES", "ConsoleSink", "GitHubIssueSink", "SinkDelivery", "comment_body"]
