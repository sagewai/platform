# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Sealed-iii.C — DurableWorkflow snapshot capture + code_hash at enqueue."""
from __future__ import annotations

from sagewai.core.state import (
    DurableWorkflow,
    ExecutionMode,
    InMemoryStore,
    StepStatus,
)
from sagewai.sealed.replay import compute_code_hash, hash_secret_value


async def test_enqueue_populates_code_hash():
    wf = DurableWorkflow(name="wf", store=InMemoryStore())

    @wf.step("s1")
    async def s1(x: str) -> str:
        return x

    rid = await wf.enqueue(input_data={"x": "1"})
    run = await wf.get_run(rid)
    assert run.code_hash == compute_code_hash(wf)


async def test_run_captures_no_snapshot_for_mode_zero():
    """A pure Mode 0 step (no Sealed cascade in scope) gets no snapshot."""
    wf = DurableWorkflow(name="wf2", store=InMemoryStore())

    @wf.step("s1")
    async def s1(x: str) -> str:
        return x.upper()

    await wf.run(x="abc")
    runs = await wf.list_runs(StepStatus.COMPLETED)
    assert len(runs) == 1
    rec = runs[0].steps["s1"]
    assert rec.status == StepStatus.COMPLETED
    assert rec.injection_snapshot is None


async def test_run_captures_snapshot_when_run_has_sealed_keys(monkeypatch):
    """When a run was enqueued with effective_secret_keys, the step
    captures an InjectionSnapshot recording those keys + a hash beacon."""
    wf = DurableWorkflow(name="wf3", store=InMemoryStore())

    @wf.step("s1")
    async def s1(x: str) -> str:
        return x

    rid = await wf.enqueue(
        input_data={"x": "ok"},
        execution_mode=ExecutionMode.IDENTITY,
    )
    run = await wf.get_run(rid)
    run.effective_env_keys = ["AWS_REGION", "AWS_ACCESS_KEY_ID"]
    run.effective_secret_keys = ["AWS_ACCESS_KEY_ID"]
    run.security_profile_ref = "builtin://acme"
    await wf._store.save_run(run)

    fake_hash = hash_secret_value("AWS-VAL")

    async def _fake_provenance(profile_id, secret_keys):
        return (
            {k: fake_hash for k in secret_keys},
            {k: None for k in secret_keys},
            {},
        )

    monkeypatch.setattr(
        "sagewai.core.state._snapshot_secret_provenance",
        _fake_provenance,
    )

    await wf.run(run_id=rid, x="ok")
    final = await wf.get_run(rid)
    snap = final.steps["s1"].injection_snapshot
    assert snap is not None
    assert snap.effective_env_keys == ["AWS_REGION", "AWS_ACCESS_KEY_ID"]
    assert snap.effective_secret_keys == ["AWS_ACCESS_KEY_ID"]
    assert snap.secret_value_hashes == {"AWS_ACCESS_KEY_ID": fake_hash}
