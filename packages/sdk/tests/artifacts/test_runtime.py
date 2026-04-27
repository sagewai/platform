# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for sagewai.artifacts.runtime — Plan ART."""
from __future__ import annotations

import pytest

from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationConfigError,
    ArtifactDestinationType,
    ArtifactUploadError,
    ArtifactUploadResult,
)
from sagewai.artifacts.runtime import apply_artifact_destination
from sagewai.core.state import ExecutionMode


class _FakeAuditWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, **kwargs) -> None:
        self.events.append(kwargs)


class _FakeUploader:
    def __init__(self, *, ok: bool = True, type=ArtifactDestinationType.LOCAL) -> None:
        self.type = type
        self._ok = ok
        self.calls: list[dict] = []

    async def validate(self, destination):
        return None

    async def upload(self, **kwargs):
        self.calls.append(kwargs)
        if not self._ok:
            raise ArtifactUploadError("simulated subprocess failure")
        return ArtifactUploadResult(
            type=self.type,
            target=kwargs["destination"].target,
            bytes_uploaded=512,
            duration_ms=12,
            ref="/host/output",
        )


@pytest.fixture(autouse=True)
def _swap_local_uploader(monkeypatch):
    """Replace the registered local uploader with a controllable fake.

    All tests in this module use destinations of type LOCAL, so the swap
    is safe and other suites are unaffected (registry is process-global).
    """
    from sagewai.artifacts import refs as refs_module

    fake = _FakeUploader()
    original = refs_module._UPLOADERS.get(ArtifactDestinationType.LOCAL)
    refs_module._UPLOADERS[ArtifactDestinationType.LOCAL] = fake
    yield fake
    if original is not None:
        refs_module._UPLOADERS[ArtifactDestinationType.LOCAL] = original


def _local_dest(env_keys=None) -> ArtifactDestination:
    return ArtifactDestination(
        type=ArtifactDestinationType.LOCAL,
        target="/host/output",
        env_keys=env_keys or [],
    )


@pytest.mark.asyncio
async def test_none_destination_returns_none():
    audit = _FakeAuditWriter()
    result = await apply_artifact_destination(
        handle=object(),                   # not consulted when destination=None
        destination=None,
        run_id="r1",
        workspace_path="/workspace",
        execution_mode=ExecutionMode.FULL,
        effective_secret_keys=set(),
        audit_writer=audit,
    )
    assert result is None
    assert audit.events == []


@pytest.mark.asyncio
async def test_mode_mismatch_emits_warning_and_skips_upload(_swap_local_uploader):
    audit = _FakeAuditWriter()
    fake = _swap_local_uploader
    result = await apply_artifact_destination(
        handle=object(),
        destination=_local_dest(),
        run_id="r-mode",
        workspace_path="/workspace",
        execution_mode=ExecutionMode.IDENTITY,
        effective_secret_keys=set(),
        audit_writer=audit,
    )
    assert result is None
    assert fake.calls == []                 # uploader never invoked
    assert any(e["event_type"] == "artifact.mode_mismatch" for e in audit.events)


@pytest.mark.asyncio
async def test_drift_detection_raises_and_emits(_swap_local_uploader):
    audit = _FakeAuditWriter()
    fake = _swap_local_uploader
    dest = _local_dest(env_keys=["MISSING_TOKEN"])

    with pytest.raises(ArtifactDestinationConfigError):
        await apply_artifact_destination(
            handle=object(),
            destination=dest,
            run_id="r-drift",
            workspace_path="/workspace",
            execution_mode=ExecutionMode.FULL,
            effective_secret_keys=set(),    # drifted away from MISSING_TOKEN
            audit_writer=audit,
        )
    assert fake.calls == []
    assert any(
        e["event_type"] == "artifact.drift_at_injection" for e in audit.events
    )


@pytest.mark.asyncio
async def test_happy_path_emits_started_then_uploaded(_swap_local_uploader):
    audit = _FakeAuditWriter()
    fake = _swap_local_uploader
    dest = _local_dest()

    result = await apply_artifact_destination(
        handle=object(),
        destination=dest,
        run_id="r-ok",
        workspace_path="/workspace",
        execution_mode=ExecutionMode.FULL,
        effective_secret_keys=set(),
        audit_writer=audit,
    )
    assert result is not None
    assert result.bytes_uploaded == 512
    types_in_order = [e["event_type"] for e in audit.events]
    assert types_in_order == ["artifact.upload.started", "artifact.uploaded"]
    assert fake.calls and fake.calls[0]["destination"] == dest


@pytest.mark.asyncio
async def test_failure_emits_started_then_failed_and_reraises(_swap_local_uploader):
    audit = _FakeAuditWriter()
    fake = _swap_local_uploader
    fake._ok = False
    dest = _local_dest()

    with pytest.raises(ArtifactUploadError):
        await apply_artifact_destination(
            handle=object(),
            destination=dest,
            run_id="r-fail",
            workspace_path="/workspace",
            execution_mode=ExecutionMode.FULL,
            effective_secret_keys=set(),
            audit_writer=audit,
        )
    types_in_order = [e["event_type"] for e in audit.events]
    assert types_in_order == ["artifact.upload.started", "artifact.upload.failed"]
    assert "simulated" in audit.events[1]["details"]["error"]


@pytest.mark.asyncio
async def test_no_audit_writer_is_harmless(_swap_local_uploader):
    """Hook works without an audit writer (graceful degrade)."""
    dest = _local_dest()
    result = await apply_artifact_destination(
        handle=object(),
        destination=dest,
        run_id="r-no-audit",
        workspace_path="/workspace",
        execution_mode=ExecutionMode.FULL,
        effective_secret_keys=set(),
        audit_writer=None,
    )
    assert result is not None
    assert result.bytes_uploaded == 512
