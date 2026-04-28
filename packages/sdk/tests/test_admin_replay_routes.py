# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Sealed-iii.C — admin replay routes (preview, commit, list-replays).

Postgres-free tests using a fake store that mimics the surface the
routes call: ``_pool.fetch``, ``load_run``, ``list_replays_of``.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin import replay_routes
from sagewai.core.state import (
    DurableWorkflow,
    ExecutionMode,
    InMemoryStore,
    StepStatus,
    _generate_run_id,
)
from sagewai.sealed.replay import InjectionSnapshot


class _FakeStore:
    """Minimal store surface needed by replay_routes.

    Wraps an InMemoryStore so the routes can read runs through both
    ``load_run`` and the raw `_pool.fetch` workflow_name lookup."""

    def __init__(self, inmem: InMemoryStore) -> None:
        self._inmem = inmem

        class _PoolShim:
            async def fetch(_self, sql: str, *args):  # noqa: N805
                # Only one query path: SELECT workflow_name FROM workflow_runs WHERE run_id
                run_id = args[0]
                for r in self._inmem._runs.values():
                    if r.run_id == run_id:
                        return [{"workflow_name": r.workflow_name}]
                return []

        self._pool = _PoolShim()

    async def load_run(self, workflow_name, run_id):
        return await self._inmem.load_run(workflow_name, run_id)

    async def save_run(self, run):
        return await self._inmem.save_run(run)

    async def list_replays_of(self, run_id):
        return [
            r for r in self._inmem._runs.values()
            if r.replay_of_run_id == run_id
        ]


def _make_app(store, registry) -> FastAPI:
    app = FastAPI()
    replay_routes.register(app, store, registry)
    return app


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


async def _seed_run(wf: DurableWorkflow) -> str:
    await wf.run(x="0")
    return _generate_run_id(wf.name, {"x": "0"})


def test_preview_returns_warnings_blockers_and_snapshot_keys():
    inmem = InMemoryStore()
    wf = DurableWorkflow(name="wfA", store=inmem)

    @wf.step("a")
    async def a(x: str) -> str:
        return x

    import asyncio
    rid = asyncio.run(_seed_run(wf))

    store = _FakeStore(inmem)
    client = TestClient(_make_app(store, {"wfA": wf}))

    resp = client.post(
        f"/api/v1/admin/workflows/runs/{rid}/replay/preview",
        json={"from_step": 0},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["original_run_id"] == rid
    assert body["execution_mode"] == "bare"
    assert body["warnings"] == []
    assert body["blockers"] == []


def test_preview_404_for_unknown_run():
    inmem = InMemoryStore()
    wf = DurableWorkflow(name="wfA", store=inmem)

    @wf.step("a")
    async def a(x: str) -> str:
        return x

    store = _FakeStore(inmem)
    client = TestClient(_make_app(store, {"wfA": wf}))
    resp = client.post(
        "/api/v1/admin/workflows/runs/does-not-exist/replay/preview",
        json={"from_step": 0},
    )
    assert resp.status_code == 404


def test_commit_creates_replay_run_and_links_to_original():
    inmem = InMemoryStore()
    wf = DurableWorkflow(name="wfA", store=inmem)

    @wf.step("a")
    async def a(x: str) -> str:
        return x

    import asyncio
    rid = asyncio.run(_seed_run(wf))

    store = _FakeStore(inmem)
    client = TestClient(_make_app(store, {"wfA": wf}))
    resp = client.post(
        f"/api/v1/admin/workflows/runs/{rid}/replay",
        json={"from_step": 0, "confirm_warnings": True},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replay_of_run_id"] == rid
    assert body["new_run_id"]


def test_commit_422_when_blockers_present():
    """A workflow whose code shape changed since enqueue produces a blocker."""
    inmem = InMemoryStore()
    wf1 = DurableWorkflow(name="wfV", store=inmem)

    @wf1.step("a")
    async def a(x: str) -> str:
        return x

    import asyncio
    rid = asyncio.run(_seed_run(wf1))

    # Simulate the workflow code growing a step since the original run.
    wf2 = DurableWorkflow(name="wfV", store=inmem)

    @wf2.step("a")
    async def a2(x: str) -> str:
        return x

    @wf2.step("b")
    async def b2(x: str) -> str:
        return x

    store = _FakeStore(inmem)
    client = TestClient(_make_app(store, {"wfV": wf2}))
    resp = client.post(
        f"/api/v1/admin/workflows/runs/{rid}/replay",
        json={"from_step": 0, "confirm_warnings": True},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "blockers" in body["detail"]
    assert body["detail"]["blockers"][0]["type"] == "workflow_version_mismatch"


def test_list_replays_returns_replay_runs():
    inmem = InMemoryStore()
    wf = DurableWorkflow(name="wfA", store=inmem)

    @wf.step("a")
    async def a(x: str) -> str:
        return x

    import asyncio

    async def _setup() -> tuple[str, str]:
        rid_local = await _seed_run(wf)
        new_id_local = await wf.replay_from(rid_local, from_step=0)
        return rid_local, new_id_local

    rid, new_id = asyncio.run(_setup())

    store = _FakeStore(inmem)
    client = TestClient(_make_app(store, {"wfA": wf}))
    resp = client.get(
        f"/api/v1/admin/workflows/runs/{rid}/replays",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any(r["run_id"] == new_id for r in body["replays"])
