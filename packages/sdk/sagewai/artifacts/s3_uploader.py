# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""S3Uploader — aws s3 sync via SandboxHandle.exec."""
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


class S3Uploader:
    """Sync /workspace contents to s3://<bucket>/<prefix> via aws s3 sync."""

    type = ArtifactDestinationType.S3

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
        s3_uri = f"s3://{destination.target.rstrip('/')}/"
        region = destination.options.get("region")
        storage_class = destination.options.get("storage_class")

        extra_args = []
        if storage_class:
            extra_args.extend(["--storage-class", shlex.quote(storage_class)])

        region_prefix = ""
        if region:
            region_prefix = f"export AWS_DEFAULT_REGION={shlex.quote(region)}\n"

        bash = f"""
set -e
{region_prefix}cd {shlex.quote(workspace_path)}
WORKSPACE_BYTES=$(find . -type f -exec wc -c {{}} + 2>/dev/null | tail -1 | awk '{{print $1+0}}')
aws s3 sync . {shlex.quote(s3_uri)} \\
    --metadata sagewai-run-id={shlex.quote(run_id)} \\
    --delete {' '.join(extra_args)}
echo "{_RESULT_MARKER}"
echo "${{WORKSPACE_BYTES:-0}}"
echo {shlex.quote(s3_uri)}
""".strip()

        started = time.monotonic()
        result = await handle.exec(
            ToolCall(
                tool="bash",
                args={"command": bash},
                call_id=f"art-s3-{uuid.uuid4().hex[:8]}",
                timeout_s=900.0,
            ),
        )
        duration_ms = int((time.monotonic() - started) * 1000)

        if not result.ok or (result.exit_code is not None and result.exit_code != 0):
            raise ArtifactUploadError(
                f"s3 sync failed (exit={result.exit_code}): "
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
            "s3 upload did not emit the result marker; "
            "stdout did not contain expected bytes/uri block",
        ) from exc
    after = [line.strip() for line in lines[idx + 1 :] if line.strip()]
    if len(after) < 2:
        raise ArtifactUploadError("s3 upload result block missing bytes or uri line")
    try:
        bytes_uploaded = int(after[0])
    except ValueError as exc:
        raise ArtifactUploadError(
            f"s3 upload returned non-integer bytes value: {after[0]!r}",
        ) from exc
    return bytes_uploaded, after[1]
