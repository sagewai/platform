# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for DurableWorkflow.enqueue artifact destination cascade — Plan ART."""
from __future__ import annotations

import pytest

from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationConfigError,
    ArtifactDestinationType,
)
from sagewai.core.state import (
    DurableWorkflow,
    ExecutionMode,
    InMemoryStore,
)


def _gh(target: str = "https://github.com/acme/portfolio.git") -> ArtifactDestination:
    return ArtifactDestination(
        type=ArtifactDestinationType.GITHUB,
        target=target,
        env_keys=[],
    )


def _local() -> ArtifactDestination:
    return ArtifactDestination(
        type=ArtifactDestinationType.LOCAL,
        target="/host/output",
        env_keys=[],
    )


@pytest.mark.asyncio
async def test_enqueue_with_no_destination_persists_none():
    store = InMemoryStore()
    wf = DurableWorkflow(name="art-test-none", store=store)
    run_id = await wf.enqueue(execution_mode=ExecutionMode.FULL)

    saved = await store.load_run("art-test-none", run_id)
    assert saved is not None
    assert saved.artifact_destination is None


@pytest.mark.asyncio
async def test_enqueue_run_override_persists_to_run():
    store = InMemoryStore()
    wf = DurableWorkflow(name="art-test-run-override", store=store)
    dest = _gh()
    run_id = await wf.enqueue(
        execution_mode=ExecutionMode.FULL,
        artifact_destination=dest,
    )

    saved = await store.load_run("art-test-run-override", run_id)
    assert saved is not None
    assert saved.artifact_destination == dest


@pytest.mark.asyncio
async def test_enqueue_code_default_persists_when_no_run_override():
    store = InMemoryStore()

    class WfWithCodeDefault(DurableWorkflow):
        artifact_destination = _local()

    wf = WfWithCodeDefault(name="art-test-code-default", store=store)
    run_id = await wf.enqueue(execution_mode=ExecutionMode.FULL)

    saved = await store.load_run("art-test-code-default", run_id)
    assert saved is not None
    assert saved.artifact_destination is not None
    assert saved.artifact_destination.type == ArtifactDestinationType.LOCAL


@pytest.mark.asyncio
async def test_enqueue_run_override_beats_code_default():
    store = InMemoryStore()

    class WfWithCodeDefault(DurableWorkflow):
        artifact_destination = _local()

    wf = WfWithCodeDefault(name="art-test-precedence", store=store)
    override = _gh()
    run_id = await wf.enqueue(
        execution_mode=ExecutionMode.FULL,
        artifact_destination=override,
    )

    saved = await store.load_run("art-test-precedence", run_id)
    assert saved is not None
    assert saved.artifact_destination == override


@pytest.mark.asyncio
async def test_enqueue_validates_env_keys_against_sealed_cascade():
    """env_keys not in resolved effective_secret_keys → enqueue raises."""
    store = InMemoryStore()
    wf = DurableWorkflow(name="art-test-env-keys", store=store)

    # Sealed cascade is empty (no profile refs), so effective_secret_keys = ().
    # A destination requiring env_keys=['MISSING'] must fail validation.
    bad = ArtifactDestination(
        type=ArtifactDestinationType.GITHUB,
        target="https://github.com/acme/x.git",
        env_keys=["MISSING_KEY"],
    )
    with pytest.raises(ArtifactDestinationConfigError):
        await wf.enqueue(
            execution_mode=ExecutionMode.FULL,
            artifact_destination=bad,
        )


@pytest.mark.asyncio
async def test_enqueue_mode_mismatch_warns_but_proceeds(caplog):
    """Destination set on Mode 0/1/2 run → warning + proceeds + persists."""
    store = InMemoryStore()
    wf = DurableWorkflow(name="art-test-mode-mismatch", store=store)
    dest = _gh()
    with caplog.at_level("WARNING"):
        run_id = await wf.enqueue(
            execution_mode=ExecutionMode.IDENTITY,
            artifact_destination=dest,
        )

    saved = await store.load_run("art-test-mode-mismatch", run_id)
    assert saved is not None
    # Destination is still persisted; the runtime hook is what skips upload
    assert saved.artifact_destination == dest
    assert any(
        "non-Mode-3+ run" in record.message for record in caplog.records
    ), "expected mode-mismatch warning in log"
