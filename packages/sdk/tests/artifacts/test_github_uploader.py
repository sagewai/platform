# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for GitHubUploader — Plan ART."""
from __future__ import annotations

import pytest

from sagewai.artifacts.github_uploader import GitHubUploader, _github_repo_path
from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationConfigError,
    ArtifactDestinationType,
    ArtifactUploadError,
)
from sagewai.sandbox.models import ToolCall, ToolResult


class _FakeHandle:
    """Minimal SandboxHandle for the uploader tests.

    Records every ToolCall it receives and replies with a configurable
    ToolResult. The Protocol is structural, so we only need ``exec``.
    """

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
async def test_validate_accepts_valid_https_target():
    up = GitHubUploader()
    await up.validate(
        ArtifactDestination(
            type=ArtifactDestinationType.GITHUB,
            target="https://github.com/acme/portfolio.git",
        ),
    )


@pytest.mark.asyncio
async def test_validate_rejects_non_github_target():
    up = GitHubUploader()
    with pytest.raises(ArtifactDestinationConfigError):
        await up.validate(
            ArtifactDestination(
                type=ArtifactDestinationType.GITHUB,
                target="https://gitlab.com/acme/portfolio.git",
            ),
        )


@pytest.mark.asyncio
async def test_upload_happy_path_parses_sha_and_bytes():
    sha = "abc123def456"
    bytes_count = 4096
    stdout = (
        "everything else\n"
        f"==SAGEWAI-ART-RESULT==\n{bytes_count}\n{sha}\n"
    )
    handle = _FakeHandle(stdout=stdout)
    up = GitHubUploader()
    dest = ArtifactDestination(
        type=ArtifactDestinationType.GITHUB,
        target="https://github.com/acme/portfolio.git",
        env_keys=["GITHUB_TOKEN"],
        options={"branch": "main"},
    )
    result = await up.upload(
        handle=handle, destination=dest,
        workspace_path="/workspace", run_id="run-x",
    )
    assert result.bytes_uploaded == bytes_count
    assert result.ref == sha
    assert result.target == dest.target
    assert result.type == ArtifactDestinationType.GITHUB
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_upload_does_not_log_token_in_argv():
    """The bash command must contain ${GITHUB_TOKEN} literally — never the value."""
    handle = _FakeHandle(
        stdout="==SAGEWAI-ART-RESULT==\n100\nabc\n",
    )
    up = GitHubUploader()
    dest = ArtifactDestination(
        type=ArtifactDestinationType.GITHUB,
        target="https://github.com/acme/portfolio.git",
        env_keys=["GITHUB_TOKEN"],
    )
    await up.upload(
        handle=handle, destination=dest,
        workspace_path="/workspace", run_id="run-y",
    )
    # The bash command sent to the sandbox must have the literal placeholder
    assert len(handle.calls) == 1
    cmd = handle.calls[0].args["command"]
    assert "${GITHUB_TOKEN}" in cmd
    # And must NOT contain anything that looks like a real PAT (40+ char hex
    # is one heuristic, but more importantly the token simply isn't on the
    # host side at all)
    assert "ghp_" not in cmd
    assert "github_pat_" not in cmd


@pytest.mark.asyncio
async def test_upload_failure_raises_with_stderr():
    handle = _FakeHandle(
        ok=False,
        exit_code=128,
        stderr="fatal: Authentication failed for 'https://github.com/...'",
    )
    up = GitHubUploader()
    dest = ArtifactDestination(
        type=ArtifactDestinationType.GITHUB,
        target="https://github.com/acme/portfolio.git",
        env_keys=["GITHUB_TOKEN"],
    )
    with pytest.raises(ArtifactUploadError) as exc:
        await up.upload(
            handle=handle, destination=dest,
            workspace_path="/workspace", run_id="run-z",
        )
    assert "Authentication failed" in str(exc.value)


@pytest.mark.asyncio
async def test_upload_missing_result_marker_raises():
    handle = _FakeHandle(stdout="git push: oh no, no marker\n")
    up = GitHubUploader()
    dest = ArtifactDestination(
        type=ArtifactDestinationType.GITHUB,
        target="https://github.com/acme/portfolio.git",
        env_keys=["GITHUB_TOKEN"],
    )
    with pytest.raises(ArtifactUploadError):
        await up.upload(
            handle=handle, destination=dest,
            workspace_path="/workspace", run_id="run-q",
        )


def test_repo_path_extraction_normalises_to_dotgit():
    assert _github_repo_path(
        "https://github.com/acme/portfolio.git",
    ) == "acme/portfolio.git"
    assert _github_repo_path(
        "https://github.com/acme/portfolio",
    ) == "acme/portfolio.git"
    assert _github_repo_path(
        "git@github.com:acme/portfolio.git",
    ) == "acme/portfolio.git"
