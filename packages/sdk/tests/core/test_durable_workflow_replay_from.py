# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Sealed-iii.C — DurableWorkflow.replay_from happy path + guards.

Covers Tasks 13 (happy path), 14 (validation guards: legacy run, code-hash
mismatch, Mode-3b callbacks), and the audit-emission piece of Task 15
that fires from replay_from itself."""
from __future__ import annotations

import pytest

from sagewai.core.state import (
    DurableWorkflow,
    ExecutionMode,
    InMemoryStore,
    StepStatus,
    _generate_run_id,
)
from sagewai.sealed.replay import (
    InjectionSnapshot,
    LegacyRunNoSnapshotError,
    ModeNotReplayableError,
    WorkflowVersionMismatchError,
)


def _make_snap() -> InjectionSnapshot:
    return InjectionSnapshot(
        effective_env_keys=["X"],
        effective_secret_keys=["X"],
        security_profile_ref="builtin://p",
        secret_value_hashes={"X": "h"},
        secret_value_versions={"X": None},
        revocations_active_at_step={},
        captured_at=1.0,
    )


# --- Task 13: happy path ----------------------------------------------------


async def test_replay_from_step_zero_creates_new_run_with_snapshots():
    wf = DurableWorkflow(name="wf", store=InMemoryStore())

    @wf.step("a")
    async def a(x: str) -> str:
        return f"a-{x}"

    @wf.step("b")
    async def b(x: str) -> str:
        return f"b-{x}"

    await wf.run(x="0"); rid = _generate_run_id(wf.name, {"x": "0"})
    original = await wf.get_run(rid)
    snap = _make_snap()
    for s in original.steps.values():
        s.injection_snapshot = snap
    await wf._store.save_run(original)

    new_id = await wf.replay_from(rid, from_step=0)
    new_run = await wf.get_run(new_id)

    assert new_run.replay_of_run_id == rid
    assert new_run.replay_from_step == 0
    assert new_run.code_hash == original.code_hash
    for name in ["a", "b"]:
        assert new_run.steps[name].status == StepStatus.PENDING
        assert new_run.steps[name].injection_snapshot == snap


async def test_replay_from_mid_workflow_preserves_completed_steps():
    wf = DurableWorkflow(name="wf2", store=InMemoryStore())

    @wf.step("a")
    async def a(x: str) -> str:
        return "A-OUT"

    @wf.step("b")
    async def b(x: str) -> str:
        return "B-OUT"

    @wf.step("c")
    async def c(x: str) -> str:
        return "C-OUT"

    await wf.run(x="0"); rid = _generate_run_id(wf.name, {"x": "0"})
    new_id = await wf.replay_from(rid, from_step=2)
    new_run = await wf.get_run(new_id)

    assert new_run.steps["a"].status == StepStatus.COMPLETED
    assert new_run.steps["a"].result == "A-OUT"
    assert new_run.steps["b"].status == StepStatus.COMPLETED
    assert new_run.steps["c"].status == StepStatus.PENDING


async def test_replay_from_step_out_of_range_raises():
    wf = DurableWorkflow(name="wf3", store=InMemoryStore())

    @wf.step("a")
    async def a(x: str) -> str:
        return x

    await wf.run(x="0"); rid = _generate_run_id(wf.name, {"x": "0"})
    with pytest.raises(ValueError, match="from_step"):
        await wf.replay_from(rid, from_step=5)


async def test_replay_from_unknown_run_raises():
    wf = DurableWorkflow(name="wf4", store=InMemoryStore())
    with pytest.raises(ValueError, match="not found"):
        await wf.replay_from("does-not-exist", from_step=0)


# --- Task 14: validation guards --------------------------------------------


async def test_legacy_run_no_snapshot_raises():
    """A run with execution_mode > BARE but no injection_snapshot on a
    pre-from_step step is treated as legacy."""
    wf = DurableWorkflow(name="wfL", store=InMemoryStore())

    @wf.step("a")
    async def a(x: str) -> str:
        return x

    @wf.step("b")
    async def b(x: str) -> str:
        return x

    await wf.run(x="0"); rid = _generate_run_id(wf.name, {"x": "0"})
    run = await wf.get_run(rid)
    run.execution_mode = ExecutionMode.IDENTITY
    for s in run.steps.values():
        s.injection_snapshot = None
    await wf._store.save_run(run)

    with pytest.raises(LegacyRunNoSnapshotError):
        await wf.replay_from(rid, from_step=1)


async def test_workflow_version_mismatch_raises():
    store = InMemoryStore()
    wf1 = DurableWorkflow(name="wfV", store=store)

    @wf1.step("a")
    async def a(x: str) -> str:
        return x

    await wf1.run(x="0"); rid = _generate_run_id(wf1.name, {"x": "0"})

    # Same store, same workflow name, but a different step shape.
    wf2 = DurableWorkflow(name="wfV", store=store)

    @wf2.step("a")
    async def a2(x: str) -> str:
        return x

    @wf2.step("b")
    async def b2(x: str) -> str:
        return x

    with pytest.raises(WorkflowVersionMismatchError):
        await wf2.replay_from(rid, from_step=0)


async def test_mode_3b_with_callback_raises():
    """When the original run has the callbacks-present sentinel, replay
    raises ModeNotReplayableError."""
    wf = DurableWorkflow(name="wfJ", store=InMemoryStore())

    @wf.step("a")
    async def a(x: str) -> str:
        return x

    await wf.run(x="0"); rid = _generate_run_id(wf.name, {"x": "0"})
    run = await wf.get_run(rid)
    run.execution_mode = ExecutionMode.FULL_JIT
    run.signals["__test_callbacks_present__"] = True
    await wf._store.save_run(run)

    with pytest.raises(ModeNotReplayableError):
        await wf.replay_from(rid, from_step=0)


# --- Task 15 piece: replay.completed fires when replay run finishes -------


async def test_replay_completed_fires_on_replay_run_finish():
    """A replay run that finishes COMPLETED should set replay_of_run_id
    on the audit event. Uses InMemoryStore — AuditWriter best-effort
    emission is tolerant of stores without `_pool`, so we assert by
    monkeypatching the writer's emit."""
    wf = DurableWorkflow(name="wfR", store=InMemoryStore())

    @wf.step("a")
    async def a(x: str) -> str:
        return x

    await wf.run(x="0"); rid = _generate_run_id(wf.name, {"x": "0"})
    new_id = await wf.replay_from(rid, from_step=0)

    captured: list[tuple[str, dict]] = []

    class _Writer:
        def __init__(self, *args, **kwargs): pass
        async def emit(self, *, event_type, **kwargs):
            captured.append((event_type, kwargs))

    import sagewai.sealed.audit as audit_mod
    orig = audit_mod.AuditWriter
    audit_mod.AuditWriter = _Writer
    try:
        await wf.run(run_id=new_id, x="0")
    finally:
        audit_mod.AuditWriter = orig

    types = {e[0] for e in captured}
    assert "replay.completed" in types
    completed = next(e for e in captured if e[0] == "replay.completed")
    assert completed[1]["details"]["original_run_id"] == rid
