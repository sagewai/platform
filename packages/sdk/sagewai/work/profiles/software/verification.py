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

import os
import re
import shlex
import shutil
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from sagewai.artifacts import LocalArtifactStore
from sagewai.fleet.execution import WorkerProcessResult, run_worker_subprocess
from sagewai.sandbox.backend import SandboxBackend
from sagewai.sandbox.models import (
    NetworkPolicy,
    ResourceLimits,
    SandboxLifetime,
    ToolCall,
)
from sagewai.work.knowledge import KnowledgeItem, KnowledgeKind, KnowledgeStore
from sagewai.work.models import OperatorDisciplineReport, VerificationResult, WorkItem
from sagewai.work.profiles.software.models import (
    SoftwareVerificationCheck,
    SoftwareWorkspace,
)
from sagewai.work.runtime import OperatorResult, WorkRequest, Workspace

_VERIFICATION_INLINE_LIMIT_BYTES = 4000
SOFTWARE_VERIFICATION_ISOLATION_PRECONDITION_ID = "software.verification.isolation"
_DIGEST_PINNED_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


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
            output_tokens=result.output_tokens,
            changed_files=len(changed_files),
            diff_lines=diff_lines,
            verdict=verdict,
        )


async def _git_changes(workspace: SoftwareWorkspace) -> tuple[tuple[str, ...], int]:
    with tempfile.TemporaryDirectory(prefix="sagewai-git-inspection-") as root:
        trusted = Path(root) / "repository"
        await _prepare_trusted_repository(workspace, trusted)
        tracked = await _checked_git(
            trusted,
            f"--work-tree={workspace.path}",
            "diff",
            "--numstat",
            "--no-ext-diff",
            "--no-textconv",
            workspace.base_sha,
            "--",
        )
        untracked = await _checked_git(
            trusted,
            f"--work-tree={workspace.path}",
            "ls-files",
            "--others",
            "--exclude-standard",
            *_trusted_exclude_args(workspace.repository),
        )

    files: set[str] = set()
    diff_lines = 0
    for line in tracked.splitlines():
        added, deleted, changed_file = line.split("\t", 2)
        files.add(changed_file)
        if added != "-":
            diff_lines += int(added)
        if deleted != "-":
            diff_lines += int(deleted)
    for changed_file in untracked.splitlines():
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


class VerificationIsolationError(RuntimeError):
    """Raised when verification cannot run inside its required boundary."""


class VerificationCommandRunner(Protocol):
    """Execute configured verification commands without host access."""

    async def run(
        self,
        *,
        project_id: str,
        work_id: str,
        attempt_id: str,
        workspace: SoftwareWorkspace,
        commands: tuple[tuple[str, ...], ...],
        timeout: float,
    ) -> tuple[WorkerProcessResult, ...]: ...


def _docker_backend() -> SandboxBackend:
    try:
        from sagewai.sandbox.docker_backend import DockerBackend

        return DockerBackend()
    except (ImportError, RuntimeError) as exc:
        raise VerificationIsolationError(
            "Docker verification backend is unavailable; install sagewai[sandbox]"
        ) from exc


class SandboxedVerificationRunner:
    """Run commands in a networkless sandbox over a disposable Git snapshot."""

    def __init__(
        self,
        *,
        image: str,
        backend_factory: Callable[[], SandboxBackend] = _docker_backend,
        resource_limits: ResourceLimits | None = None,
    ) -> None:
        if not _DIGEST_PINNED_IMAGE.fullmatch(image):
            raise ValueError("verification sandbox image must be digest-pinned")
        self._image = image
        self._backend_factory = backend_factory
        self._resource_limits = resource_limits or ResourceLimits()

    async def run(
        self,
        *,
        project_id: str,
        work_id: str,
        attempt_id: str,
        workspace: SoftwareWorkspace,
        commands: tuple[tuple[str, ...], ...],
        timeout: float,
    ) -> tuple[WorkerProcessResult, ...]:
        backend: SandboxBackend | None = None
        try:
            backend = self._backend_factory()
        except Exception as exc:
            raise VerificationIsolationError(
                f"verification sandbox backend is unavailable: {exc}"
            ) from exc
        handle = None
        try:
            try:
                with tempfile.TemporaryDirectory(prefix="sagewai-verification-") as root:
                    try:
                        snapshot = Path(root) / "workspace"
                        await _create_verification_snapshot(workspace, snapshot)
                        try:
                            handle = await backend.start(
                                project_id=project_id,
                                run_id=f"verify-{work_id}-{attempt_id}",
                                image=self._image,
                                image_digest=_image_digest(self._image),
                                env={},
                                network_policy=NetworkPolicy.NONE,
                                resource_limits=self._resource_limits,
                                workdir_mount=snapshot,
                                lifetime=SandboxLifetime.PER_RUN,
                                user=f"{os.getuid()}:{os.getgid()}",
                            )
                        except Exception as exc:
                            raise VerificationIsolationError(
                                f"verification sandbox failed to start: {exc}"
                            ) from exc

                        results: list[WorkerProcessResult] = []
                        for index, argv in enumerate(commands, start=1):
                            call = ToolCall(
                                tool="bash",
                                args={"command": shlex.join(argv)},
                                call_id=f"verify-{attempt_id}-{index}",
                                timeout_s=timeout,
                            )
                            try:
                                receipt = await handle.exec(call)
                            except Exception as exc:
                                raise VerificationIsolationError(
                                    f"verification sandbox execution failed: {exc}"
                                ) from exc
                            if (
                                receipt.call_id != call.call_id
                                or receipt.error is not None
                                or receipt.exit_code is None
                                or receipt.ok != (receipt.exit_code == 0)
                            ):
                                detail = receipt.error or "inconsistent status and exit code"
                                raise VerificationIsolationError(
                                    "invalid verification sandbox execution receipt: "
                                    f"{detail}"
                                )
                            results.append(
                                WorkerProcessResult(
                                    returncode=receipt.exit_code,
                                    stdout=receipt.stdout,
                                    stderr=receipt.stderr,
                                )
                            )
                        return tuple(results)
                    finally:
                        if handle is not None:
                            try:
                                await handle.stop()
                            except Exception as exc:
                                raise VerificationIsolationError(
                                    f"verification sandbox cleanup failed: stop: {exc}"
                                ) from exc
                            finally:
                                handle = None
            except VerificationIsolationError:
                raise
            except Exception as exc:
                raise VerificationIsolationError(
                    f"verification isolation failed: {exc}"
                ) from exc
        finally:
            try:
                await backend.close()
            except Exception as exc:
                raise VerificationIsolationError(
                    f"verification sandbox cleanup failed: close: {exc}"
                ) from exc


def _image_digest(image: str) -> str:
    return "sha256:" + image.rsplit("@sha256:", 1)[1]


def _trusted_git_env() -> dict[str, str]:
    return {
        "HOME": os.devnull,
        "XDG_CONFIG_HOME": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _trusted_git_argv(*args: str) -> tuple[str, ...]:
    return (
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        *args,
    )


async def _prepare_trusted_repository(
    workspace: SoftwareWorkspace,
    destination: Path,
) -> None:
    repository = workspace.repository.resolve()
    if not repository.is_dir():
        raise VerificationIsolationError("trusted repository is unavailable")
    clone = await run_worker_subprocess(
        argv=_trusted_git_argv(
            "clone",
            "--quiet",
            "--no-hardlinks",
            "--no-checkout",
            str(repository),
            str(destination),
        ),
        cwd=repository.parent,
        explicit_env=_trusted_git_env(),
        output_limit=None,
    )
    if clone.returncode != 0:
        raise VerificationIsolationError(f"cannot prepare trusted repository: {clone.stderr}")
    await _checked_git(
        destination,
        "checkout",
        "--quiet",
        "--detach",
        workspace.base_sha,
    )


def _trusted_exclude_args(repository: Path) -> tuple[str, ...]:
    git_dir = repository / ".git"
    if not git_dir.is_dir() and (repository / "objects").is_dir():
        git_dir = repository
    exclude = git_dir / "info" / "exclude"
    return (f"--exclude-from={exclude}",) if exclude.is_file() else ()


async def _create_verification_snapshot(
    workspace: SoftwareWorkspace,
    destination: Path,
) -> None:
    await _prepare_trusted_repository(workspace, destination)
    diff = await _checked_git(
        destination,
        f"--work-tree={workspace.path}",
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        workspace.base_sha,
        "--",
    )
    if diff:
        await _checked_git(
            destination,
            "apply",
            "--binary",
            "--whitespace=nowarn",
            "--",
            stdin=diff,
        )

    untracked = await _checked_git(
        destination,
        f"--work-tree={workspace.path}",
        "ls-files",
        "--others",
        "--exclude-standard",
        *_trusted_exclude_args(workspace.repository),
        "-z",
    )
    for relative in filter(None, untracked.split("\0")):
        _copy_untracked(workspace.path, destination, relative)
    await _checked_git(destination, "remote", "remove", "origin")


async def _checked_git(cwd: Path, *args: str, stdin: str = "") -> str:
    result = await run_worker_subprocess(
        argv=_trusted_git_argv(*args),
        stdin=stdin,
        explicit_env=_trusted_git_env(),
        cwd=cwd,
        output_limit=None,
    )
    if result.returncode != 0:
        raise VerificationIsolationError(
            f"git {' '.join(args)} failed while preparing verification: {result.stderr}"
        )
    return result.stdout


def _copy_untracked(source: Path, destination: Path, relative: str) -> None:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise VerificationIsolationError(f"unsafe untracked path: {relative}")
    src = source.joinpath(*path.parts)
    if src.is_dir():
        raise VerificationIsolationError(f"untracked directory is not allowed: {relative}")
    dst = destination.joinpath(*path.parts)
    current = destination
    for part in path.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise VerificationIsolationError(f"untracked path crosses symlink: {relative}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        dst.symlink_to(os.readlink(src))
    else:
        shutil.copy2(src, dst, follow_symlinks=False)


class SoftwareVerifier:
    """Run configured verification commands and publish immutable receipts."""

    def __init__(
        self,
        *,
        knowledge_store: KnowledgeStore,
        runner: VerificationCommandRunner,
        artifact_store: LocalArtifactStore | None = None,
        timeout: float = 600,
    ) -> None:
        self._knowledge_store = knowledge_store
        self._runner = runner
        self._artifact_store = artifact_store
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

        parsed_commands: list[tuple[str, ...]] = []
        for command in commands:
            argv = tuple(shlex.split(command))
            if not argv:
                raise ValueError("verification command cannot be empty")
            parsed_commands.append(argv)
        processes = await self._runner.run(
            project_id=work_item.project_id,
            work_id=work_item.id,
            attempt_id=attempt_id,
            workspace=workspace,
            commands=tuple(parsed_commands),
            timeout=self._timeout,
        )
        if len(processes) != len(commands):
            raise ValueError("verification runner returned an unexpected result count")

        checks: list[SoftwareVerificationCheck] = []
        evidence_refs: list[str] = []
        passed = True
        for index, (command, process) in enumerate(zip(commands, processes), start=1):
            output = f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}"
            output_bytes = output.encode()
            artifact_ref = None
            artifact_refs: tuple[str, ...] = ()
            if len(output_bytes) > _VERIFICATION_INLINE_LIMIT_BYTES:
                if self._artifact_store is None:
                    self._artifact_store = LocalArtifactStore()
                artifact = self._artifact_store.put_bytes(
                    output_bytes,
                    media_type="text/plain",
                    created_by="software.verifier",
                )
                artifact_ref = artifact.storage_ref
                artifact_refs = (artifact_ref,)
                evidence = f"artifact_ref: {artifact_ref}"
            else:
                evidence = output
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
                    f"{evidence}"
                ),
                source_refs=(source_ref,),
                artifact_refs=artifact_refs,
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
                    artifact_ref=artifact_ref,
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
            output_tokens=result.output_tokens,
            changed_files=0,
            diff_lines=0,
            verdict="blocked" if violations else "pass",
        )
