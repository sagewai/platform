# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic result checks for the software Work profile."""

from __future__ import annotations

import shlex
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Literal

from sagewai.fleet.execution import run_worker_subprocess
from sagewai.work.knowledge import KnowledgeItem, KnowledgeKind, KnowledgeStore
from sagewai.work.models import OperatorDisciplineReport, VerificationResult, WorkItem
from sagewai.work.profiles.software.models import (
    SoftwareVerificationCheck,
    SoftwareWorkspace,
    WorkspaceStaleError,
)
from sagewai.work.profiles.software.scm import _git
from sagewai.work.runtime import OperatorResult, WorkRequest, Workspace


class SoftwareResultValidator:
    """Validate actual Git effects against the declared ActionScope and intents."""

    async def validate(
        self,
        *,
        request: WorkRequest,
        result: OperatorResult,
        workspace: Workspace | None,
    ) -> OperatorDisciplineReport:
        if not isinstance(workspace, SoftwareWorkspace):
            raise ValueError("software result validation requires a software workspace")
        if (
            request.project_id != workspace.project_id
            or request.work_id != workspace.work_id
            or result.project_id != request.project_id
            or result.work_id != request.work_id
            or result.run_id != request.run_id
        ):
            raise ValueError("result validation inputs belong to different work")

        changed_files, diff_lines = await _git_changes(workspace)
        scope_violations: list[str] = []
        scope = request.action_scope

        for changed_file in changed_files:
            if scope.allowed_targets and not any(
                _within_target(changed_file, target) for target in scope.allowed_targets
            ):
                scope_violations.append(f"{changed_file} is outside allowed targets")
            if any(_within_target(changed_file, target) for target in scope.forbidden_targets):
                scope_violations.append(f"{changed_file} is forbidden")
            if not any(
                _intent_declares_file(intent.target, intent.scope, changed_file)
                for intent in request.action_intents
            ):
                scope_violations.append(f"undeclared change: {changed_file}")

        if scope.max_files_changed is not None and len(changed_files) > scope.max_files_changed:
            scope_violations.append(
                f"{len(changed_files)} changed files exceeds {scope.max_files_changed}"
            )
        if scope.max_diff_lines is not None and diff_lines > scope.max_diff_lines:
            scope_violations.append(f"{diff_lines} diff lines exceeds {scope.max_diff_lines}")

        declared_action_ids = {intent.action_id for intent in request.action_intents}
        receipt_ids = {receipt.action_id for receipt in result.action_results}
        for action_id in sorted(receipt_ids - declared_action_ids):
            scope_violations.append(f"undeclared action result: {action_id}")
        for action_id in sorted(declared_action_ids - receipt_ids):
            scope_violations.append(f"missing action result: {action_id}")

        verdict: Literal["pass", "blocked"] = "blocked" if scope_violations else "pass"
        return OperatorDisciplineReport(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            unsupported_claims=(),
            scope_violations=tuple(scope_violations),
            permission_violations=(),
            risk_mismatches=(),
            unnecessary_changes=(),
            output_tokens=None,
            changed_files=len(changed_files),
            diff_lines=diff_lines,
            verdict=verdict,
        )


async def _git_changes(workspace: SoftwareWorkspace) -> tuple[tuple[str, ...], int]:
    tracked = await _git(workspace.path, "diff", "--numstat", workspace.base_sha, "--")
    if tracked.returncode != 0:
        raise WorkspaceStaleError(tracked.stderr)
    untracked = await _git(
        workspace.path,
        "ls-files",
        "--others",
        "--exclude-standard",
    )
    if untracked.returncode != 0:
        raise WorkspaceStaleError(untracked.stderr)

    files: set[str] = set()
    diff_lines = 0
    for line in tracked.stdout.splitlines():
        added, deleted, changed_file = line.split("\t", 2)
        files.add(changed_file)
        if added != "-":
            diff_lines += int(added)
        if deleted != "-":
            diff_lines += int(deleted)
    for changed_file in untracked.stdout.splitlines():
        files.add(changed_file)
        try:
            diff_lines += len((workspace.path / changed_file).read_text().splitlines())
        except UnicodeDecodeError:
            pass
    return tuple(sorted(files)), diff_lines


def _normalized_target(value: str) -> str:
    return str(PurePosixPath(value)).rstrip("/")


def _within_target(changed_file: str, target: str) -> bool:
    target = _normalized_target(target)
    if target in {"", "."}:
        return True
    changed_file = _normalized_target(changed_file)
    return changed_file == target or changed_file.startswith(f"{target}/")


def _intent_declares_file(target: str, scope: dict, changed_file: str) -> bool:
    if _within_target(changed_file, target):
        return True
    allowed = scope.get("allowed_targets", ())
    return isinstance(allowed, list | tuple) and any(
        isinstance(value, str) and _within_target(changed_file, value) for value in allowed
    )


class SoftwareVerifier:
    """Run configured verification commands and publish immutable receipts."""

    def __init__(
        self,
        *,
        knowledge_store: KnowledgeStore,
        timeout: float = 600,
    ) -> None:
        self._knowledge_store = knowledge_store
        self._timeout = timeout

    async def verify(
        self,
        *,
        work_item: WorkItem,
        attempt_id: str,
        workspace: SoftwareWorkspace,
        commands: tuple[str, ...],
    ) -> VerificationResult:
        """Execute every command; the process receipts alone determine the verdict."""
        if work_item.project_id is None:
            raise ValueError("software verification requires a project")
        if workspace.project_id != work_item.project_id or workspace.work_id != work_item.id:
            raise ValueError("verification workspace belongs to different work")
        if not commands:
            raise ValueError("at least one verification command is required")

        checks: list[SoftwareVerificationCheck] = []
        evidence_refs: list[str] = []
        passed = True
        for index, command in enumerate(commands, start=1):
            argv = tuple(shlex.split(command))
            if not argv:
                raise ValueError("verification command cannot be empty")
            process = await run_worker_subprocess(
                argv=argv,
                cwd=workspace.path,
                timeout=self._timeout,
                output_limit=4000,
            )
            source_ref = f"command://{work_item.id}/{attempt_id}/{index}"
            item = KnowledgeItem(
                id=str(uuid.uuid4()),
                project_id=work_item.project_id,
                work_id=work_item.id,
                kind=KnowledgeKind.ACTION_RESULT,
                statement=(
                    f"command: {command}\n"
                    f"exit_code: {process.returncode}\n"
                    f"timed_out: {str(process.timed_out).lower()}\n"
                    f"stdout:\n{process.stdout}\n"
                    f"stderr:\n{process.stderr}"
                ),
                source_refs=(source_ref,),
                factness_score=100,
                created_by="software.verifier",
                created_at=datetime.now(timezone.utc),
            )
            await self._knowledge_store.publish(item)
            evidence_refs.append(item.id)
            checks.append(
                SoftwareVerificationCheck(
                    name=f"command-{index}",
                    command=command,
                    exit_code=process.returncode,
                    artifact_ref=None,
                )
            )
            passed = passed and process.returncode == 0 and not process.timed_out

        return VerificationResult(
            attempt_id=attempt_id,
            passed=passed,
            evidence_refs=tuple(evidence_refs),
            profile_context={"checks": [check.model_dump(mode="json") for check in checks]},
        )


class SoftwareReadOnlyResultValidator:
    """Reject action receipts from a review stage that declares no actions."""

    async def validate(
        self,
        *,
        request: WorkRequest,
        result: OperatorResult,
        workspace: Workspace | None,
    ) -> OperatorDisciplineReport:
        violations = ("review returned action results",) if result.action_results else ()
        if not isinstance(workspace, SoftwareWorkspace):
            raise ValueError("software review validation requires a software workspace")
        if request.project_id != workspace.project_id or request.work_id != workspace.work_id:
            raise ValueError("review validation inputs belong to different work")

        return OperatorDisciplineReport(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            unsupported_claims=(),
            scope_violations=violations,
            permission_violations=(),
            risk_mismatches=(),
            unnecessary_changes=(),
            output_tokens=None,
            changed_files=0,
            diff_lines=0,
            verdict="blocked" if violations else "pass",
        )
