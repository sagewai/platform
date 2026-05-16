# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""ArtifactUploader Protocol — implemented by GitHub / S3 / Local uploaders."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationType,
    ArtifactUploadResult,
)

if TYPE_CHECKING:
    from sagewai.sandbox.backend import SandboxHandle


@runtime_checkable
class ArtifactUploader(Protocol):
    """Strategy for uploading /workspace contents to a typed destination."""

    type: ArtifactDestinationType

    async def validate(self, destination: ArtifactDestination) -> None:
        """Per-type structural validation; raises ArtifactDestinationConfigError."""
        ...

    async def upload(
        self,
        *,
        handle: SandboxHandle,
        destination: ArtifactDestination,
        workspace_path: str,
        run_id: str,
    ) -> ArtifactUploadResult:
        """Run the upload as subprocess(es) via handle.exec.

        Reads credentials from the sandbox env (Sealed-injected). Plaintext
        never crosses the sandbox boundary; only the result metadata
        (bytes, duration, ref) is returned to the host.
        """
        ...
