# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Central Git workspace transport for software stages dispatched through Fleet."""

from __future__ import annotations

import base64
import hashlib
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.fleet.execution import run_worker_subprocess
from sagewai.work.fleet import FleetWorkspaceTransfer, FleetWorkspaceTransferResult
from sagewai.work.profiles.software.models import SoftwareWorkspace
from sagewai.work.profiles.software.scm import (
    SoftwareWorktreeManager,
    WorkspaceStaleError,
    workspace_diff,
)
from sagewai.work.runtime import Workspace


def repository_ref_from_origin(origin: str) -> str:
    """Return a credential-free stable identity for one configured Git origin."""
    value = origin.strip()
    if not value:
        raise ValueError("Git origin must be non-empty")
    parsed = urlsplit(value) if "://" in value else None
    if parsed is not None and parsed.hostname is not None:
        host = parsed.hostname.lower()
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        path = parsed.path.strip("/")
        normalized = f"{host}/{path}"
    else:
        scp = re.fullmatch(r"(?:[^@/]+@)?([^:]+):(.+)", value)
        normalized = (
            f"{scp.group(1).lower()}/{scp.group(2).strip(chr(47))}" if scp is not None else value
        )
    normalized = normalized.removesuffix(".git").rstrip("/")
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return f"git-origin://sha256:{digest}"


async def software_repository_ref(repository: Path) -> str:
    """Read a trusted local origin and return only its credential-free identity."""
    result = await run_worker_subprocess(
        argv=("git", "remote", "get-url", "origin"),
        cwd=repository,
        output_limit=None,
    )
    if result.returncode != 0:
        raise WorkspaceStaleError(result.stderr or "Git origin is unavailable")
    return repository_ref_from_origin(result.stdout)


SOFTWARE_FLEET_WORKSPACE_KIND = "software.git-diff.v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_REPOSITORY_REF_PATTERN = r"^git-origin://sha256:[0-9a-f]{64}$"


class SoftwareFleetWorkspaceInput(BaseModel):
    """Git-specific input carried inside the generic Fleet transfer payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository_ref: str = Field(pattern=_REPOSITORY_REF_PATTERN)
    base_sha: str
    current_sha: str
    cumulative_diff_base64: str

    @model_validator(mode="after")
    def validate_diff(self) -> SoftwareFleetWorkspaceInput:
        try:
            base64.b64decode(self.cumulative_diff_base64, validate=True)
        except ValueError as exc:
            raise ValueError("cumulative diff is not valid base64") from exc
        return self


class SoftwareFleetWorkspaceOutput(BaseModel):
    """Git-specific worker delta carried inside the generic result payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository_ref: str = Field(pattern=_REPOSITORY_REF_PATTERN)
    base_sha: str
    current_sha: str
    delta_diff_base64: str
    delta_diff_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_delta(self) -> SoftwareFleetWorkspaceOutput:
        try:
            content = base64.b64decode(self.delta_diff_base64, validate=True)
        except ValueError as exc:
            raise ValueError("delta diff is not valid base64") from exc
        if hashlib.sha256(content).hexdigest() != self.delta_diff_sha256:
            raise ValueError("delta diff digest does not match")
        return self


class SoftwareFleetWorkspaceTransport:
    """Snapshot and safely apply worker-returned Git deltas on the canonical worktree."""

    def __init__(self, *, repository_ref: str) -> None:
        if re.fullmatch(_REPOSITORY_REF_PATTERN, repository_ref) is None:
            raise ValueError("repository_ref must be a credential-safe Git origin digest")
        self._repository_ref = repository_ref
        self._worktrees = SoftwareWorktreeManager()

    async def snapshot(self, workspace: Workspace) -> FleetWorkspaceTransfer:
        software_workspace = self._software_workspace(workspace)
        current_sha = await self._worktrees.current_sha(software_workspace)
        transfer_workspace = software_workspace.model_copy(update={"base_sha": current_sha})
        cumulative_diff, _ = await workspace_diff(transfer_workspace)
        content = cumulative_diff.encode()
        payload = SoftwareFleetWorkspaceInput(
            repository_ref=self._repository_ref,
            base_sha=software_workspace.base_sha,
            current_sha=current_sha,
            cumulative_diff_base64=base64.b64encode(content).decode("ascii"),
        )
        return FleetWorkspaceTransfer(
            ref=software_workspace.ref,
            project_id=software_workspace.project_id,
            work_id=software_workspace.work_id,
            kind=SOFTWARE_FLEET_WORKSPACE_KIND,
            input_digest=hashlib.sha256(content).hexdigest(),
            payload=payload.model_dump(mode="json"),
        )

    async def apply(
        self,
        workspace: Workspace,
        snapshot: FleetWorkspaceTransfer,
        result: FleetWorkspaceTransferResult,
    ) -> None:
        software_workspace = self._software_workspace(workspace)
        input_payload = SoftwareFleetWorkspaceInput.model_validate(snapshot.payload)
        output_payload = SoftwareFleetWorkspaceOutput.model_validate(result.payload)
        self._validate_identity(
            software_workspace,
            snapshot,
            result,
            input_payload,
            output_payload,
        )
        current_sha = await self._worktrees.current_sha(software_workspace)
        if current_sha != input_payload.current_sha:
            raise WorkspaceStaleError("canonical workspace HEAD changed after Fleet dispatch")

        transfer_workspace = software_workspace.model_copy(
            update={"base_sha": input_payload.current_sha}
        )
        current_diff, _ = await workspace_diff(transfer_workspace)
        current_digest = hashlib.sha256(current_diff.encode()).hexdigest()
        delta = base64.b64decode(output_payload.delta_diff_base64, validate=True).decode()
        if current_digest == result.result_digest:
            if current_digest == result.input_digest and delta:
                raise WorkspaceStaleError("unchanged Fleet workspace result includes a delta")
            return
        if current_digest != snapshot.input_digest or result.input_digest != snapshot.input_digest:
            raise WorkspaceStaleError("canonical workspace diff changed after Fleet dispatch")

        starting_diff = base64.b64decode(
            input_payload.cumulative_diff_base64, validate=True
        ).decode()
        await self._validate_result_diff(
            software_workspace,
            current_sha=input_payload.current_sha,
            starting_diff=starting_diff,
            delta=delta,
            expected_digest=result.result_digest,
        )
        if delta:
            await self._apply_patch(software_workspace.path, delta)
        applied_diff, _ = await workspace_diff(transfer_workspace)
        if hashlib.sha256(applied_diff.encode()).hexdigest() != result.result_digest:
            raise WorkspaceStaleError("applied Fleet workspace digest does not match result")

    async def _validate_result_diff(
        self,
        workspace: SoftwareWorkspace,
        *,
        current_sha: str,
        starting_diff: str,
        delta: str,
        expected_digest: str,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="sagewai-fleet-validate-") as root:
            path = Path(root) / "workspace"
            added = await run_worker_subprocess(
                argv=(
                    "git",
                    "worktree",
                    "add",
                    "--detach",
                    str(path),
                    current_sha,
                ),
                cwd=workspace.repository,
                output_limit=None,
            )
            if added.returncode != 0:
                raise WorkspaceStaleError(added.stderr)
            try:
                if starting_diff:
                    await self._apply_patch(path, starting_diff)
                if delta:
                    await self._apply_patch(path, delta)
                validation_workspace = workspace.model_copy(
                    update={"path": path, "base_sha": current_sha}
                )
                resulting_diff, _ = await workspace_diff(validation_workspace)
                if hashlib.sha256(resulting_diff.encode()).hexdigest() != expected_digest:
                    raise WorkspaceStaleError(
                        "returned Fleet workspace digest does not match delta"
                    )
            finally:
                removed = await run_worker_subprocess(
                    argv=("git", "worktree", "remove", "--force", str(path)),
                    cwd=workspace.repository,
                    output_limit=None,
                )
                if removed.returncode != 0:
                    raise WorkspaceStaleError(removed.stderr)

    @staticmethod
    async def _apply_patch(path: Path, diff: str) -> None:
        applied = await run_worker_subprocess(
            argv=("git", "apply", "--binary", "--whitespace=nowarn", "--"),
            stdin=diff,
            cwd=path,
            output_limit=None,
        )
        if applied.returncode != 0:
            raise WorkspaceStaleError(applied.stderr)

    def _validate_identity(
        self,
        workspace: SoftwareWorkspace,
        snapshot: FleetWorkspaceTransfer,
        result: FleetWorkspaceTransferResult,
        input_payload: SoftwareFleetWorkspaceInput,
        output_payload: SoftwareFleetWorkspaceOutput,
    ) -> None:
        if (
            snapshot.kind != SOFTWARE_FLEET_WORKSPACE_KIND
            or result.kind != snapshot.kind
            or snapshot.ref != workspace.ref
            or snapshot.project_id != workspace.project_id
            or snapshot.work_id != workspace.work_id
            or input_payload.repository_ref != self._repository_ref
            or input_payload.base_sha != workspace.base_sha
            or result.ref != snapshot.ref
            or result.project_id != snapshot.project_id
            or result.work_id != snapshot.work_id
            or output_payload.repository_ref != input_payload.repository_ref
            or output_payload.base_sha != input_payload.base_sha
            or output_payload.current_sha != input_payload.current_sha
        ):
            raise ValueError("Fleet workspace identity does not match canonical workspace")

    @staticmethod
    def _software_workspace(workspace: Workspace) -> SoftwareWorkspace:
        if not isinstance(workspace, SoftwareWorkspace):
            raise TypeError("software Fleet transport requires SoftwareWorkspace")
        return workspace
