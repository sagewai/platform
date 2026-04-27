# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""End-to-end: NullBackend + LocalUploader + apply_artifact_destination.

Exercises the full Plan ART runtime path without Postgres / Docker:

  1. Acquire a NullBackend sandbox with a tmp workspace.
  2. Write a known file to the workspace via handle.exec.
  3. Drive apply_artifact_destination to upload to a separate tmp dir.
  4. Assert the file landed at the destination.
  5. Assert the audit pipeline saw started + uploaded events.

This is the proof-of-correctness for the "Mode 3+ workflow can push to
a local path" verification line — Mode 2-style sandbox covers the same
runtime contract that future per-step CLI dispatch will satisfy.
"""
from __future__ import annotations

import pytest

from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationType,
)
from sagewai.artifacts.runtime import apply_artifact_destination
from sagewai.core.state import ExecutionMode
from sagewai.sandbox.models import (
    NetworkPolicy,
    ResourceLimits,
    SandboxLifetime,
    ToolCall,
)
from sagewai.sandbox.null_backend import NullBackend


class _CapturingAuditWriter:
    """Audit writer that captures events in-memory for assertion."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, **kwargs) -> None:
        self.events.append(kwargs)


@pytest.mark.asyncio
async def test_local_uploader_end_to_end(tmp_path):
    workspace = tmp_path / "workspace"
    destination_dir = tmp_path / "host-output"

    backend = NullBackend()
    handle = await backend.start(
        project_id="test",
        run_id="run-art-e2e-local",
        image="null",
        image_digest="",
        env={},
        network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(),
        workdir_mount=workspace,
        lifetime=SandboxLifetime.PER_RUN,
    )

    # Write a known file inside the workspace via the sandbox.
    write_result = await handle.exec(
        ToolCall(
            tool="bash",
            args={"command": "echo 'hello from sagewai' > artifact.txt"},
            call_id="seed-1",
            timeout_s=10.0,
        ),
    )
    assert write_result.ok, write_result.error or write_result.stderr

    audit = _CapturingAuditWriter()
    destination = ArtifactDestination(
        type=ArtifactDestinationType.LOCAL,
        target=str(destination_dir),
        env_keys=[],
    )

    result = await apply_artifact_destination(
        handle=handle,
        destination=destination,
        run_id="run-art-e2e-local",
        workspace_path=str(workspace),
        execution_mode=ExecutionMode.FULL,
        effective_secret_keys=set(),
        audit_writer=audit,
        audit_context={"workflow_name": "art-e2e", "project_id": "test"},
    )

    # Upload result mirrors what landed on disk.
    assert result is not None
    assert result.type == ArtifactDestinationType.LOCAL
    assert result.target == str(destination_dir)
    assert result.bytes_uploaded > 0
    assert result.duration_ms >= 0

    # File copied through to the destination.
    landed = destination_dir / "artifact.txt"
    assert landed.exists(), f"expected {landed}"
    assert landed.read_text().strip() == "hello from sagewai"

    # Audit pipeline saw the started → uploaded pair.
    types = [e["event_type"] for e in audit.events]
    assert types == ["artifact.upload.started", "artifact.uploaded"]

    started, uploaded = audit.events
    assert started["details"]["type"] == "local"
    assert started["details"]["target"] == str(destination_dir)
    assert uploaded["details"]["bytes_uploaded"] == result.bytes_uploaded
    assert uploaded["details"]["ref"] == str(destination_dir)


@pytest.mark.asyncio
async def test_skipped_when_execution_mode_is_not_mode_3_plus(tmp_path):
    """A Mode 2 (IDENTITY) run with a destination set logs a mismatch and skips."""
    workspace = tmp_path / "workspace"
    destination_dir = tmp_path / "host-output"
    workspace.mkdir()

    backend = NullBackend()
    handle = await backend.start(
        project_id="test",
        run_id="run-art-mode2",
        image="null",
        image_digest="",
        env={},
        network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(),
        workdir_mount=workspace,
        lifetime=SandboxLifetime.PER_RUN,
    )

    audit = _CapturingAuditWriter()
    destination = ArtifactDestination(
        type=ArtifactDestinationType.LOCAL,
        target=str(destination_dir),
        env_keys=[],
    )

    result = await apply_artifact_destination(
        handle=handle,
        destination=destination,
        run_id="run-art-mode2",
        workspace_path=str(workspace),
        execution_mode=ExecutionMode.IDENTITY,
        effective_secret_keys=set(),
        audit_writer=audit,
    )

    assert result is None
    assert any(e["event_type"] == "artifact.mode_mismatch" for e in audit.events)
    # No upload happened
    assert not (destination_dir / "artifact.txt").exists()
