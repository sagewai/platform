# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for LocalUploader — Plan ART."""
from __future__ import annotations

import pytest

from sagewai.artifacts.local_uploader import LocalUploader
from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationConfigError,
    ArtifactDestinationType,
    ArtifactUploadError,
)
from sagewai.sandbox.models import ToolCall, ToolResult


class _FakeHandle:
    def __init__(self, *, stdout: str = "", ok: bool = True, exit_code: int = 0,
                 stderr: str = "", error: str | None = None) -> None:
        self.calls: list[ToolCall] = []
        self._stdout = stdout
        self._ok = ok
        self._exit_code = exit_code
        self._stderr = stderr
        self._error = error
        self.sandbox_id = "fake"
        self.mode = None
        self.image = "fake"
        self.image_digest = ""

    async def exec(self, tool_call: ToolCall) -> ToolResult:
        self.calls.append(tool_call)
        return ToolResult(
            call_id=tool_call.call_id,
            ok=self._ok,
            exit_code=self._exit_code,
            stdout=self._stdout,
            stderr=self._stderr,
            error=self._error,
        )


@pytest.mark.asyncio
async def test_validate_accepts_absolute_path():
    up = LocalUploader()
    await up.validate(
        ArtifactDestination(
            type=ArtifactDestinationType.LOCAL, target="/host/output",
        ),
    )


@pytest.mark.asyncio
async def test_validate_rejects_relative_or_empty():
    up = LocalUploader()
    for bad in ["relative/path", "", "./local"]:
        with pytest.raises(ArtifactDestinationConfigError):
            await up.validate(
                ArtifactDestination(
                    type=ArtifactDestinationType.LOCAL, target=bad,
                ),
            )


@pytest.mark.asyncio
async def test_upload_happy_path_uses_cp_command():
    handle = _FakeHandle(
        stdout="==SAGEWAI-ART-RESULT==\n2048\n/host/output\n",
    )
    up = LocalUploader()
    dest = ArtifactDestination(
        type=ArtifactDestinationType.LOCAL,
        target="/host/output",
        env_keys=[],
    )
    result = await up.upload(
        handle=handle, destination=dest,
        workspace_path="/workspace", run_id="run-1",
    )
    assert result.bytes_uploaded == 2048
    assert result.ref == "/host/output"

    cmd = handle.calls[0].args["command"]
    assert "cp -R" in cmd
    assert "/host/output" in cmd
    assert "/workspace" in cmd


@pytest.mark.asyncio
async def test_upload_preserve_workspace_false_clears_destination():
    handle = _FakeHandle(
        stdout="==SAGEWAI-ART-RESULT==\n100\n/host/output\n",
    )
    up = LocalUploader()
    dest = ArtifactDestination(
        type=ArtifactDestinationType.LOCAL,
        target="/host/output",
        options={"preserve_workspace": "false"},
    )
    await up.upload(
        handle=handle, destination=dest,
        workspace_path="/workspace", run_id="run-q",
    )
    cmd = handle.calls[0].args["command"]
    assert "rm -rf" in cmd


@pytest.mark.asyncio
async def test_upload_failure_surfaces_stderr():
    handle = _FakeHandle(
        ok=False,
        exit_code=1,
        stderr="cp: cannot create directory '/host/nope': Permission denied",
    )
    up = LocalUploader()
    dest = ArtifactDestination(
        type=ArtifactDestinationType.LOCAL, target="/host/nope",
    )
    with pytest.raises(ArtifactUploadError) as exc:
        await up.upload(
            handle=handle, destination=dest,
            workspace_path="/workspace", run_id="run-x",
        )
    assert "Permission denied" in str(exc.value)


@pytest.mark.asyncio
async def test_upload_missing_marker_raises():
    handle = _FakeHandle(stdout="cp done\n")
    up = LocalUploader()
    dest = ArtifactDestination(
        type=ArtifactDestinationType.LOCAL, target="/host/output",
    )
    with pytest.raises(ArtifactUploadError):
        await up.upload(
            handle=handle, destination=dest,
            workspace_path="/workspace", run_id="run-q",
        )
