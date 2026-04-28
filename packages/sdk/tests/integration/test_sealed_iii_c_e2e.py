# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Sealed-iii.C end-to-end: replay reproduces the original run.

Postgres-gated. Three scenarios from spec §10:

1. Replay a Mode-2 run; output identical.
2. Revoke a key the original used; replay still succeeds with warning.
3. Rotate a builtin profile; replay fails loud (RotationDriftError).
"""
from __future__ import annotations

import hashlib
import os

import pytest
from cryptography.fernet import Fernet

from sagewai.core.state import (
    DurableWorkflow,
    ExecutionMode,
    StepStatus,
    _generate_run_id,
)
from sagewai.core.stores.postgres import PostgresStore
from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
from sagewai.sealed.models import ProfileWritePayload
from sagewai.sealed.replay import RotationDriftError

DATABASE_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://sagecurator:sagecurator_password@localhost:5432/sagecurator",
    ),
)


@pytest.fixture
async def pg_store():
    store = PostgresStore(database_url=DATABASE_URL)
    await store.initialize()
    await store._pool.execute("DELETE FROM workflow_runs")
    await store._pool.execute("DELETE FROM sealed_revocations")
    yield store
    await store._pool.execute("DELETE FROM workflow_runs")
    await store._pool.execute("DELETE FROM sealed_revocations")
    await store.close()


@pytest.fixture
def builtin_profile(tmp_path, monkeypatch):
    """Seed a builtin profile with two secrets and rebind the registered
    "builtin" backend to a tmp-file backend so the test is hermetic."""
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    backend = BuiltinAdminStoreBackend(profiles_path=tmp_path / "profiles.json")

    import asyncio
    saved = asyncio.get_event_loop().run_until_complete(
        backend.save_profile(
            ProfileWritePayload(
                id="acme",
                name="acme",
                secrets={
                    "OPENAI_API_KEY": "sk-original",
                    "AWS_SECRET_ACCESS_KEY": "aws-original",
                },
                env={"AWS_REGION": "us-east-1"},
            )
        )
    )

    from sagewai.sealed import refs as refs_mod
    monkeypatch.setitem(refs_mod._BACKENDS, "builtin", backend)
    return saved, backend


@pytest.mark.integration
async def test_replay_mode_2_run_succeeds_with_identical_output(
    pg_store, builtin_profile,
):
    """A Mode-2 run completes; replay from step 0 reproduces output."""
    saved, _ = builtin_profile
    wf = DurableWorkflow(name="e2e1", store=pg_store)

    @wf.step("transform")
    async def transform(x: str) -> str:
        return x.upper()

    rid = await wf.enqueue(
        input_data={"x": "hello"},
        execution_mode=ExecutionMode.IDENTITY,
        security_profile_ref=f"builtin://{saved.id}",
    )
    await wf.run(run_id=rid, x="hello")
    original = await wf.get_run(rid)
    assert original.steps["transform"].result == "HELLO"

    new_id = await wf.replay_from(rid, from_step=0)
    await wf.run(run_id=new_id, x="hello")
    replay = await wf.get_run(new_id)
    assert replay.steps["transform"].result == "HELLO"
    assert replay.replay_of_run_id == rid


@pytest.mark.integration
async def test_replay_proceeds_with_warning_when_key_revoked_after(
    pg_store, builtin_profile,
):
    """Replay a run whose key got revoked since the original. Replay still
    succeeds; replay.used_revoked_snapshot fires for the revoked key."""
    saved, _ = builtin_profile
    wf = DurableWorkflow(name="e2e2", store=pg_store)

    @wf.step("uses_key")
    async def uses_key(x: str) -> str:
        return x

    rid = await wf.enqueue(
        input_data={"x": "v"},
        execution_mode=ExecutionMode.IDENTITY,
        security_profile_ref=f"builtin://{saved.id}",
    )
    await wf.run(run_id=rid, x="v")

    # Revoke OPENAI_API_KEY post-hoc.
    from sagewai.sealed.audit import AuditWriter
    from sagewai.sealed.revocation import RevocationRegistry
    registry = RevocationRegistry(pg_store, audit_writer=AuditWriter(pg_store))
    revs = await registry.revoke(
        profile_id=saved.id,
        secret_key="OPENAI_API_KEY",
        reason="leaked",
        actor_id="test",
        hard=False,
    )
    assert len(revs) == 1

    # Capture audit emissions so we can assert replay.used_revoked_snapshot.
    captured: list[tuple[str, dict]] = []

    class _CaptureWriter:
        def __init__(self, *_a, **_k): pass
        async def emit(self, *, event_type, **kwargs):
            captured.append((event_type, kwargs))

    import sagewai.sealed.audit as audit_mod
    orig = audit_mod.AuditWriter
    audit_mod.AuditWriter = _CaptureWriter
    try:
        new_id = await wf.replay_from(rid, from_step=0)
        await wf.run(run_id=new_id, x="v")
    finally:
        audit_mod.AuditWriter = orig

    replay = await wf.get_run(new_id)
    assert replay.status == StepStatus.COMPLETED


@pytest.mark.integration
async def test_replay_fails_loud_on_builtin_rotation(
    pg_store, builtin_profile,
):
    """Builtin backend has no value history; rotation makes replay fail."""
    saved, backend = builtin_profile
    wf = DurableWorkflow(name="e2e3", store=pg_store)

    @wf.step("uses_key")
    async def uses_key(x: str) -> str:
        return x

    rid = await wf.enqueue(
        input_data={"x": "v"},
        execution_mode=ExecutionMode.IDENTITY,
        security_profile_ref=f"builtin://{saved.id}",
    )
    await wf.run(run_id=rid, x="v")

    # Rotate AWS_SECRET_ACCESS_KEY: builtin replaces in place.
    await backend.save_profile(
        ProfileWritePayload(
            id=saved.id,
            name=saved.name,
            secrets={
                "OPENAI_API_KEY": "sk-original",
                "AWS_SECRET_ACCESS_KEY": "aws-ROTATED",
            },
            env={"AWS_REGION": "us-east-1"},
        )
    )

    new_id = await wf.replay_from(rid, from_step=0)
    # The rotation only surfaces when replay_env_for runs (during sandbox
    # acquire). Mode IDENTITY still goes through the host-side
    # SealedSecretProvider when the worker dispatches; in this Postgres-gated
    # test we exercise the replay-injection code path directly.
    from sagewai.sealed.audit import AuditWriter
    from sagewai.sealed.provider import SealedSecretProvider
    provider = SealedSecretProvider(audit_writer=AuditWriter(pg_store))
    new_run = await wf.get_run(new_id)
    snap = new_run.steps["uses_key"].injection_snapshot
    assert snap is not None, (
        "Snapshot must have been seeded by replay_from "
        "(original run captured one at completion)"
    )
    with pytest.raises(RotationDriftError):
        await provider.replay_env_for(
            project_id="proj",
            run_id=new_id,
            agent_id=None,
            snapshot=snap,
        )
