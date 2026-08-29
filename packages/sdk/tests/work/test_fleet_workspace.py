# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Central software workspace transfer over the existing Fleet queue."""

from __future__ import annotations

import base64
import hashlib
import subprocess
from pathlib import Path

import pytest

from sagewai.work import FleetWorkspaceTransfer, FleetWorkspaceTransferResult
from sagewai.work.profiles.software import SoftwareWorkspace, workspace_diff
from sagewai.work.profiles.software.fleet_workspace import (
    SoftwareFleetWorkspaceInput,
    SoftwareFleetWorkspaceOutput,
    SoftwareFleetWorkspaceTransport,
    repository_ref_from_origin,
)
from sagewai.work.profiles.software.models import WorkspaceStaleError

REPOSITORY_REF = repository_ref_from_origin("git@github.com:sagewai/platform.git")


def _git(repository: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ("git", *args),
        cwd=repository,
        check=check,
        capture_output=True,
        text=True,
    ).stdout


def _workspace(tmp_path: Path) -> SoftwareWorkspace:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("base\n")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "base")
    base_sha = _git(repository, "rev-parse", "HEAD").strip()
    return SoftwareWorkspace(
        ref="workspace://attempt-1",
        project_id="project-a",
        work_id="work-1",
        attempt_id="attempt-1",
        repository=repository,
        path=repository,
        base_sha=base_sha,
        initial_sha=base_sha,
    )


async def _result_with_added_file(
    workspace: SoftwareWorkspace,
    transport: SoftwareFleetWorkspaceTransport,
) -> tuple[FleetWorkspaceTransfer, FleetWorkspaceTransferResult]:
    (workspace.path / "README.md").write_text("central change\n")
    snapshot = await transport.snapshot(workspace)
    added = workspace.path / "worker.txt"
    added.write_text("worker change\n")
    resulting_diff, _ = await workspace_diff(
        workspace.model_copy(
            update={
                "base_sha": SoftwareFleetWorkspaceInput.model_validate(snapshot.payload).current_sha
            }
        )
    )
    delta = _git(
        workspace.path,
        "diff",
        "--no-ext-diff",
        "--binary",
        "--no-index",
        "--",
        "/dev/null",
        "worker.txt",
        check=False,
    )
    added.unlink()
    delta_bytes = delta.encode()
    input_payload = SoftwareFleetWorkspaceInput.model_validate(snapshot.payload)
    output_payload = SoftwareFleetWorkspaceOutput(
        repository_ref=input_payload.repository_ref,
        base_sha=input_payload.base_sha,
        current_sha=input_payload.current_sha,
        delta_diff_base64=base64.b64encode(delta_bytes).decode("ascii"),
        delta_diff_sha256=hashlib.sha256(delta_bytes).hexdigest(),
    )
    return snapshot, FleetWorkspaceTransferResult(
        ref=snapshot.ref,
        project_id=snapshot.project_id,
        work_id=snapshot.work_id,
        kind=snapshot.kind,
        input_digest=snapshot.input_digest,
        result_digest=hashlib.sha256(resulting_diff.encode()).hexdigest(),
        payload=output_payload.model_dump(mode="json"),
    )


def test_repository_ref_is_credential_free_and_protocol_independent() -> None:
    https_ref = repository_ref_from_origin("https://oauth-secret@github.com/sagewai/platform.git")
    ssh_ref = repository_ref_from_origin("git@github.com:sagewai/platform.git")

    assert https_ref == ssh_ref == REPOSITORY_REF
    assert "oauth-secret" not in https_ref
    with pytest.raises(ValueError, match="credential-safe"):
        SoftwareFleetWorkspaceTransport(repository_ref="github://sagewai/platform")


@pytest.mark.asyncio
async def test_transport_diff_is_relative_to_current_head_after_prior_commit(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    (workspace.path / "README.md").write_text("prior committed outcome\n")
    _git(workspace.path, "add", "README.md")
    _git(workspace.path, "commit", "-m", "prior outcome")
    current_sha = _git(workspace.path, "rev-parse", "HEAD").strip()
    transport = SoftwareFleetWorkspaceTransport(repository_ref=REPOSITORY_REF)

    snapshot, result = await _result_with_added_file(workspace, transport)

    input_payload = SoftwareFleetWorkspaceInput.model_validate(snapshot.payload)
    assert input_payload.base_sha == workspace.base_sha
    assert input_payload.current_sha == current_sha
    starting_diff = base64.b64decode(input_payload.cumulative_diff_base64).decode()
    assert "-base\n" not in starting_diff
    await transport.apply(workspace, snapshot, result)
    assert (workspace.path / "worker.txt").read_text() == "worker change\n"


@pytest.mark.asyncio
async def test_transport_applies_validated_delta_once(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    transport = SoftwareFleetWorkspaceTransport(repository_ref=REPOSITORY_REF)
    snapshot, result = await _result_with_added_file(workspace, transport)

    await transport.apply(workspace, snapshot, result)
    assert (workspace.path / "README.md").read_text() == "central change\n"
    assert (workspace.path / "worker.txt").read_text() == "worker change\n"
    first_diff, _ = await workspace_diff(workspace)
    assert hashlib.sha256(first_diff.encode()).hexdigest() == result.result_digest

    resumed_snapshot = await transport.snapshot(workspace)
    assert resumed_snapshot.input_digest == result.result_digest
    assert result.input_digest != resumed_snapshot.input_digest
    await transport.apply(workspace, resumed_snapshot, result)
    second_diff, _ = await workspace_diff(workspace)
    assert second_diff == first_diff


@pytest.mark.asyncio
async def test_transport_rejects_wrong_starting_digest_without_mutation(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    transport = SoftwareFleetWorkspaceTransport(repository_ref=REPOSITORY_REF)
    snapshot, result = await _result_with_added_file(workspace, transport)
    hostile = result.model_copy(update={"input_digest": "0" * 64})

    with pytest.raises(WorkspaceStaleError, match="diff changed"):
        await transport.apply(workspace, snapshot, hostile)

    assert not (workspace.path / "worker.txt").exists()
    current_diff, _ = await workspace_diff(workspace)
    assert hashlib.sha256(current_diff.encode()).hexdigest() == snapshot.input_digest


@pytest.mark.asyncio
async def test_transport_prevalidates_result_digest_before_canonical_mutation(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    transport = SoftwareFleetWorkspaceTransport(repository_ref=REPOSITORY_REF)
    snapshot, result = await _result_with_added_file(workspace, transport)
    hostile = result.model_copy(update={"result_digest": "0" * 64})

    with pytest.raises(WorkspaceStaleError, match="does not match delta"):
        await transport.apply(workspace, snapshot, hostile)

    assert not (workspace.path / "worker.txt").exists()
    current_diff, _ = await workspace_diff(workspace)
    assert hashlib.sha256(current_diff.encode()).hexdigest() == snapshot.input_digest
