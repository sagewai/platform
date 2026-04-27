# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for S3Uploader — Plan ART."""
from __future__ import annotations

import pytest

from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationConfigError,
    ArtifactDestinationType,
    ArtifactUploadError,
)
from sagewai.artifacts.s3_uploader import S3Uploader
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
async def test_validate_accepts_bucket_and_prefix():
    up = S3Uploader()
    await up.validate(
        ArtifactDestination(
            type=ArtifactDestinationType.S3, target="my-bucket",
        ),
    )
    await up.validate(
        ArtifactDestination(
            type=ArtifactDestinationType.S3, target="my-bucket/some/prefix",
        ),
    )


@pytest.mark.asyncio
async def test_validate_rejects_scheme_or_leading_slash():
    up = S3Uploader()
    for bad in ["s3://bucket", "/bucket", "", "bucket/"]:
        with pytest.raises(ArtifactDestinationConfigError):
            await up.validate(
                ArtifactDestination(
                    type=ArtifactDestinationType.S3, target=bad,
                ),
            )


@pytest.mark.asyncio
async def test_upload_happy_path_emits_aws_sync_command():
    handle = _FakeHandle(
        stdout="==SAGEWAI-ART-RESULT==\n8192\ns3://my-bucket/prefix/\n",
    )
    up = S3Uploader()
    dest = ArtifactDestination(
        type=ArtifactDestinationType.S3,
        target="my-bucket/prefix",
        env_keys=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        options={"region": "us-east-1"},
    )
    result = await up.upload(
        handle=handle, destination=dest,
        workspace_path="/workspace", run_id="run-1",
    )
    assert result.bytes_uploaded == 8192
    assert result.ref == "s3://my-bucket/prefix/"
    assert result.type == ArtifactDestinationType.S3

    # Inspect the dispatched command — must use aws s3 sync + region + meta
    cmd = handle.calls[0].args["command"]
    assert "aws s3 sync" in cmd
    assert "s3://my-bucket/prefix/" in cmd
    assert "AWS_DEFAULT_REGION=us-east-1" in cmd
    assert "sagewai-run-id=run-1" in cmd
    assert "--delete" in cmd


@pytest.mark.asyncio
async def test_upload_failure_surfaces_stderr():
    handle = _FakeHandle(
        ok=False,
        exit_code=1,
        stderr="An error occurred (AccessDenied) when calling PutObject",
    )
    up = S3Uploader()
    dest = ArtifactDestination(
        type=ArtifactDestinationType.S3, target="bad-bucket",
        env_keys=["AWS_ACCESS_KEY_ID"],
    )
    with pytest.raises(ArtifactUploadError) as exc:
        await up.upload(
            handle=handle, destination=dest,
            workspace_path="/workspace", run_id="run-x",
        )
    assert "AccessDenied" in str(exc.value)


@pytest.mark.asyncio
async def test_upload_missing_marker_raises():
    handle = _FakeHandle(stdout="aws s3 sync done\n")
    up = S3Uploader()
    dest = ArtifactDestination(
        type=ArtifactDestinationType.S3, target="my-bucket",
        env_keys=[],
    )
    with pytest.raises(ArtifactUploadError):
        await up.upload(
            handle=handle, destination=dest,
            workspace_path="/workspace", run_id="run-q",
        )
