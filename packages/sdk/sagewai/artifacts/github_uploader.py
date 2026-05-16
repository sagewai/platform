# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""GitHubUploader — git push via SandboxHandle.exec.

The host-side bash command we dispatch contains the literal placeholder
``${GITHUB_TOKEN}`` which is expanded only inside the sandbox by the
shell. The token never appears in host-side argv, audit log, or stdout.
"""
from __future__ import annotations

import re
import shlex
import time
import uuid

from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationConfigError,
    ArtifactDestinationType,
    ArtifactUploadError,
    ArtifactUploadResult,
)
from sagewai.artifacts.validation import validate_target
from sagewai.sandbox.backend import SandboxHandle
from sagewai.sandbox.models import ToolCall

_RESULT_MARKER = "==SAGEWAI-ART-RESULT=="
_BOT_EMAIL = "bot@sagewai.ai"
_BOT_NAME = "sagewai-bot"


class GitHubUploader:
    """Push /workspace contents to a github.com repo via git."""

    type = ArtifactDestinationType.GITHUB

    async def validate(self, destination: ArtifactDestination) -> None:
        validate_target(self.type, destination.target)

    async def upload(
        self,
        *,
        handle: SandboxHandle,
        destination: ArtifactDestination,
        workspace_path: str,
        run_id: str,
    ) -> ArtifactUploadResult:
        branch = destination.options.get("branch", "main")
        commit_message = destination.options.get(
            "commit_message", f"sagewai run {run_id}",
        )
        repo_path = _github_repo_path(destination.target)

        # Build the bash sequence. ${GITHUB_TOKEN} is expanded inside the
        # sandbox; the host never materialises it. set -e aborts on any
        # nonzero step. The result markers let us parse SHA + bytes.
        # Quote the workspace + commit message safely against shell escapes.
        bash = f"""
set -e
cd {shlex.quote(workspace_path)}
WORKSPACE_BYTES=$(find . -type f -exec wc -c {{}} + 2>/dev/null | tail -1 | awk '{{print $1+0}}')
git init -q
git remote remove origin >/dev/null 2>&1 || true
git remote add origin "https://x-access-token:${{GITHUB_TOKEN}}@github.com/{repo_path}"
git -c user.email={shlex.quote(_BOT_EMAIL)} -c user.name={shlex.quote(_BOT_NAME)} add -A
git -c user.email={shlex.quote(_BOT_EMAIL)} -c user.name={shlex.quote(_BOT_NAME)} \
    commit -q -m {shlex.quote(commit_message)} --allow-empty
git push origin HEAD:{shlex.quote(branch)}
SHA=$(git rev-parse HEAD)
echo "{_RESULT_MARKER}"
echo "${{WORKSPACE_BYTES:-0}}"
echo "$SHA"
""".strip()

        started = time.monotonic()
        result = await handle.exec(
            ToolCall(
                tool="bash",
                args={"command": bash},
                call_id=f"art-github-{uuid.uuid4().hex[:8]}",
                timeout_s=300.0,
            ),
        )
        duration_ms = int((time.monotonic() - started) * 1000)

        if not result.ok or (result.exit_code is not None and result.exit_code != 0):
            raise ArtifactUploadError(
                f"github push failed (exit={result.exit_code}): "
                f"{(result.error or result.stderr or result.stdout)[:500]}",
            )

        bytes_uploaded, ref = _parse_result_block(result.stdout)
        return ArtifactUploadResult(
            type=self.type,
            target=destination.target,
            bytes_uploaded=bytes_uploaded,
            duration_ms=duration_ms,
            ref=ref,
        )


_GITHUB_HTTPS_RE = re.compile(
    r"^https://github\.com/(?P<path>[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+?)(\.git)?/?$",
)
_GITHUB_SSH_RE = re.compile(
    r"^git@github\.com:(?P<path>[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+?)\.git$",
)


def _github_repo_path(target: str) -> str:
    """Extract the '<org>/<repo>' segment from a github.com URL."""
    m = _GITHUB_HTTPS_RE.match(target) or _GITHUB_SSH_RE.match(target)
    if not m:
        raise ArtifactDestinationConfigError(
            f"could not parse github org/repo from target {target!r}",
        )
    path = m.group("path")
    if not path.endswith(".git"):
        path = path + ".git"
    return path


def _parse_result_block(stdout: str) -> tuple[int, str]:
    """Parse the trailing result block emitted by the upload bash sequence."""
    lines = stdout.splitlines()
    try:
        idx = next(
            i for i, line in enumerate(lines) if line.strip() == _RESULT_MARKER
        )
    except StopIteration as exc:
        raise ArtifactUploadError(
            "github upload did not emit the result marker; "
            "stdout did not contain expected SHA/bytes block",
        ) from exc
    after = [line.strip() for line in lines[idx + 1 :] if line.strip()]
    if len(after) < 2:
        raise ArtifactUploadError(
            "github upload result block missing bytes or SHA line",
        )
    try:
        bytes_uploaded = int(after[0])
    except ValueError as exc:
        raise ArtifactUploadError(
            f"github upload returned non-integer bytes value: {after[0]!r}",
        ) from exc
    return bytes_uploaded, after[1]
