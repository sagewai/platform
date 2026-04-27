# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LocalUploader — cp /workspace/. <target> via SandboxHandle.exec.

The convention (from docs/architecture/execution-modes.md): destinations
of type ``local`` use a path that is bind-mounted at the same path on
both the host and inside the sandbox. NullBackend has no path remapping,
so the target IS the host path. DockerBackend requires the operator to
bind-mount the destination directory at the same path inside the
sandbox; threading that into ``backend.start(host_mounts=...)`` is the
follow-up referenced in spec §13 open question 4.

Image variants without ``cp`` (extremely uncommon — coreutils is base)
will fail-loud at upload time with a clear error.
"""
from __future__ import annotations

import shlex
import time
import uuid

from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationType,
    ArtifactUploadError,
    ArtifactUploadResult,
)
from sagewai.artifacts.validation import validate_target
from sagewai.sandbox.backend import SandboxHandle
from sagewai.sandbox.models import ToolCall

_RESULT_MARKER = "==SAGEWAI-ART-RESULT=="


class LocalUploader:
    """Copy /workspace contents to a host-mounted local path."""

    type = ArtifactDestinationType.LOCAL

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
        target = destination.target
        # The operator may set options.preserve_workspace='false' to clear
        # the destination first; default keeps any pre-existing files.
        preserve = destination.options.get("preserve_workspace", "true").lower() != "false"

        clear = "" if preserve else f"rm -rf {shlex.quote(target)}/* 2>/dev/null || true\n"
        bash = f"""
set -e
cd {shlex.quote(workspace_path)}
WORKSPACE_BYTES=$(find . -type f -exec wc -c {{}} + 2>/dev/null | tail -1 | awk '{{print $1+0}}')
mkdir -p {shlex.quote(target)}
{clear}cp -R . {shlex.quote(target)}/
echo "{_RESULT_MARKER}"
echo "${{WORKSPACE_BYTES:-0}}"
echo {shlex.quote(target)}
""".strip()

        started = time.monotonic()
        result = await handle.exec(
            ToolCall(
                tool="bash",
                args={"command": bash},
                call_id=f"art-local-{uuid.uuid4().hex[:8]}",
                timeout_s=300.0,
            ),
        )
        duration_ms = int((time.monotonic() - started) * 1000)

        if not result.ok or (result.exit_code is not None and result.exit_code != 0):
            raise ArtifactUploadError(
                f"local upload failed (exit={result.exit_code}): "
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


def _parse_result_block(stdout: str) -> tuple[int, str]:
    lines = stdout.splitlines()
    try:
        idx = next(
            i for i, line in enumerate(lines) if line.strip() == _RESULT_MARKER
        )
    except StopIteration as exc:
        raise ArtifactUploadError(
            "local upload did not emit the result marker",
        ) from exc
    after = [line.strip() for line in lines[idx + 1 :] if line.strip()]
    if len(after) < 2:
        raise ArtifactUploadError(
            "local upload result block missing bytes or path line",
        )
    try:
        bytes_uploaded = int(after[0])
    except ValueError as exc:
        raise ArtifactUploadError(
            f"local upload returned non-integer bytes value: {after[0]!r}",
        ) from exc
    return bytes_uploaded, after[1]
