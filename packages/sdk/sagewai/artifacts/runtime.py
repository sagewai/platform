# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Runtime hook — call after a Mode 3+ run finishes its workspace work.

The future per-step CLI-dispatch plan calls ``apply_artifact_destination``
right after its CLI subprocess returns. Today, ART itself can be exercised
end-to-end against a Mode 2 sandbox by writing to ``workspace_path`` via
``handle.exec`` and then invoking this hook directly.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationConfigError,
    ArtifactUploadResult,
)
from sagewai.artifacts.refs import resolve_uploader
from sagewai.artifacts.validation import validate_env_keys_subset
from sagewai.core.state import ExecutionMode

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sagewai.sandbox.backend import SandboxHandle
    from sagewai.sealed.audit import AuditWriter

logger = logging.getLogger(__name__)

_MODE_3_PLUS = {ExecutionMode.FULL, ExecutionMode.FULL_JIT}


async def apply_artifact_destination(
    *,
    handle: SandboxHandle,
    destination: ArtifactDestination | None,
    run_id: str,
    workspace_path: str,
    execution_mode: ExecutionMode,
    effective_secret_keys: Iterable[str],
    audit_writer: AuditWriter | None = None,
    audit_context: dict | None = None,
) -> ArtifactUploadResult | None:
    """Run the upload step for a Mode 3+ run, or no-op for other modes.

    Returns the ArtifactUploadResult on success. None when:
      - destination is None (no upload configured)
      - run is not Mode 3+ (skip with audit warning)

    Raises:
      - ArtifactDestinationConfigError when env_keys are no longer a subset
        of the cascade-resolved effective_secret_keys (drift detection)
      - ArtifactUploadError if the uploader subprocess fails
    """
    if destination is None:
        return None

    project_id = (audit_context or {}).get("project_id")

    if execution_mode not in _MODE_3_PLUS:
        logger.warning(
            "Artifact destination set on a non-Mode-3+ run "
            "(run_id=%s, execution_mode=%s) — upload skipped.",
            run_id, execution_mode.value,
        )
        await _emit(
            audit_writer,
            event_type="artifact.mode_mismatch",
            run_id=run_id,
            project_id=project_id,
            details={
                "execution_mode": execution_mode.value,
                "destination_type": destination.type.value,
            },
            context=audit_context,
        )
        return None

    # Drift detection: the Sealed cascade may have re-resolved between
    # enqueue and sandbox-start (rotation, revocation). If so, the
    # destination's env_keys may no longer be a subset.
    try:
        validate_env_keys_subset(destination.env_keys, effective_secret_keys)
    except ArtifactDestinationConfigError as exc:
        logger.error(
            "Artifact destination drift at injection (run_id=%s): %s",
            run_id, exc,
        )
        await _emit(
            audit_writer,
            event_type="artifact.drift_at_injection",
            run_id=run_id,
            project_id=project_id,
            details={
                "expected_env_keys": sorted(destination.env_keys),
                "effective_secret_keys": sorted(effective_secret_keys),
                "destination_type": destination.type.value,
            },
            context=audit_context,
        )
        raise

    uploader = resolve_uploader(destination.type)

    await _emit(
        audit_writer,
        event_type="artifact.upload.started",
        run_id=run_id,
        project_id=project_id,
        details={
            "type": destination.type.value,
            "target": destination.target,
            "env_keys": sorted(destination.env_keys),
        },
        context=audit_context,
    )

    started = time.monotonic()
    try:
        result = await uploader.upload(
            handle=handle,
            destination=destination,
            workspace_path=workspace_path,
            run_id=run_id,
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        await _emit(
            audit_writer,
            event_type="artifact.upload.failed",
            run_id=run_id,
            project_id=project_id,
            details={
                "type": destination.type.value,
                "target": destination.target,
                "error": str(exc)[:500],
                "duration_ms": duration_ms,
            },
            context=audit_context,
        )
        raise

    await _emit(
        audit_writer,
        event_type="artifact.uploaded",
        run_id=run_id,
        project_id=project_id,
        details={
            "type": result.type.value,
            "target": result.target,
            "bytes_uploaded": result.bytes_uploaded,
            "duration_ms": result.duration_ms,
            "ref": result.ref,
            "object_count": result.object_count,
            "warnings": result.warnings,
        },
        context=audit_context,
    )
    return result


async def _emit(
    audit_writer: AuditWriter | None,
    *,
    event_type: str,
    run_id: str,
    project_id: str | None,
    details: dict,
    context: dict | None,
) -> None:
    """Audit emit with graceful failure (best-effort, never raises)."""
    if audit_writer is None:
        return
    try:
        await audit_writer.emit(
            event_type=event_type,
            actor_type="runtime",
            run_id=run_id,
            project_id=project_id,
            details=details,
            context=context,
        )
    except Exception as exc:
        logger.debug("artifact audit emit %s failed: %s", event_type, exc)
