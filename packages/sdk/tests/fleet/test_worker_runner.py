# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""WorkerRunner runtime tests against the real admin app (ASGITransport)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from sagewai.fleet.runner import WorkerRunner


@pytest.fixture
def app_token(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")
    app = create_admin_serve_app(sf)
    token = sf.validate_login("a@b.com", "pw123456")["access_token"]
    return app, token


def _runner(app, token, **kw):
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=httpx.Timeout(5.0),
    )
    kw.setdefault("name", "w1")
    kw.setdefault("models", ["gpt-4o"])
    return WorkerRunner(base_url="http://test", http_client=client, poll_timeout=1.0, **kw)


async def _approve(app, token, worker_id):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        await c.post(f"/api/v1/fleet/workers/{worker_id}/approve")


@pytest.mark.asyncio
async def test_register_appears_in_registry(app_token):
    app, token = app_token
    r = _runner(app, token, labels={"gpu": "a100"})
    wid, status = await r.register()
    assert status == "pending"
    worker = await app.state.fleet_registry.get_worker(wid)
    assert worker is not None
    assert "gpt-4o" in worker.capabilities.models_canonical
    assert worker.capabilities.labels.get("gpu") == "a100"


@pytest.mark.asyncio
async def test_claims_and_reports_completed(app_token):
    app, token = app_token
    r = _runner(app, token)
    wid, _ = await r.register()
    await _approve(app, token, wid)
    app.state.fleet_task_store.enqueue({"run_id": "r1", "model": "gpt-4o", "pool": "default"})
    result = await r.run_once()
    assert result == {"claimed": True, "run_id": "r1", "status": "completed", "reported": True}
    assert app.state.fleet_task_store._completed["r1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_exec_nonzero_reports_failed(app_token):
    app, token = app_token
    r = _runner(app, token, exec_cmd="exit 3")
    wid, _ = await r.register()
    await _approve(app, token, wid)
    app.state.fleet_task_store.enqueue({"run_id": "r2", "model": "gpt-4o", "pool": "default"})
    result = await r.run_once()
    assert result == {"claimed": True, "run_id": "r2", "status": "failed", "reported": True}


@pytest.mark.asyncio
async def test_register_only_does_not_claim(app_token):
    app, token = app_token
    r = _runner(app, token)
    wid, status = await r.register()  # register_only path = register then stop
    assert status == "pending"
    # nothing claimed because we never call run_once/run
    assert app.state.fleet_task_store._claimed == {}


@pytest.mark.asyncio
async def test_run_once_pending_worker_returns_cleanly(app_token):
    # A freshly-registered (pending) worker must not crash run_once.
    app, token = app_token
    r = _runner(app, token)
    result = await r.run_once()  # registers (pending), claim → 403 pending
    assert result == {"claimed": False, "reason": "pending"}


@pytest.mark.asyncio
async def test_report_retries_transient_5xx_then_succeeds(app_token):
    app, token = app_token
    r = _runner(app, token)
    wid, _ = await r.register()
    await _approve(app, token, wid)
    app.state.fleet_task_store.enqueue({"run_id": "r5", "model": "gpt-4o", "pool": "default"})

    # Wrap the client so the first /report attempt raises a transient error.
    real_post = r.http_client.post
    calls = {"n": 0}

    async def flaky_post(url, *a, **kw):
        if url.endswith("/api/v1/fleet/report"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom")
        return await real_post(url, *a, **kw)

    r.http_client.post = flaky_post  # type: ignore[assignment]
    result = await r.run_once()
    assert result == {"claimed": True, "run_id": "r5", "status": "completed", "reported": True}
    assert calls["n"] >= 2  # retried at least once
    assert app.state.fleet_task_store._completed["r5"]["status"] == "completed"


@pytest.mark.asyncio
async def test_report_403_is_not_retried_and_returns_false(app_token):
    app, token = app_token
    r = _runner(app, token)
    w1, _ = await r.register()
    await _approve(app, token, w1)
    r2 = _runner(app, token, name="w2")
    w2, _ = await r2.register()
    await _approve(app, token, w2)
    app.state.fleet_task_store.enqueue({"run_id": "r403", "model": "gpt-4o", "pool": "default"})
    task = await r._claim()  # claimed by w1
    assert task and task["run_id"] == "r403"

    calls = {"n": 0}
    real_post = r.http_client.post

    async def counting_post(url, *a, **kw):
        if url.endswith("/api/v1/fleet/report"):
            calls["n"] += 1
        return await real_post(url, *a, **kw)

    r.http_client.post = counting_post  # type: ignore[assignment]
    r.worker_id = w2  # impersonate → server returns 403 (not owner)
    accepted = await r._report("r403", "completed", "x", None)
    assert accepted is False
    assert calls["n"] == 1  # auth failure NOT retried


@pytest.mark.asyncio
async def test_heartbeat_fires_within_interval(app_token):
    app, token = app_token
    r = _runner(app, token, heartbeat_interval=0.05)
    wid, _ = await r.register()
    await _approve(app, token, wid)
    hb = {"n": 0}
    real_post = r.http_client.post

    async def counting_post(url, *a, **kw):
        if url.endswith("/api/v1/fleet/heartbeat"):
            hb["n"] += 1
        return await real_post(url, *a, **kw)

    r.http_client.post = counting_post  # type: ignore[assignment]

    async def _stop_soon():
        await asyncio.sleep(0.3)
        r.stop()

    await asyncio.gather(r.run(), _stop_soon())
    assert hb["n"] >= 1


@pytest.mark.asyncio
async def test_max_concurrent_is_capped(app_token):
    app, token = app_token
    r = _runner(app, token, max_concurrent=2, heartbeat_interval=0.05)
    wid, _ = await r.register()
    await _approve(app, token, wid)
    for i in range(4):
        app.state.fleet_task_store.enqueue({"run_id": f"c{i}", "model": "gpt-4o", "pool": "default"})

    peak = {"cur": 0, "max": 0}

    async def tracked_execute(task):
        peak["cur"] += 1
        peak["max"] = max(peak["max"], peak["cur"])
        await asyncio.sleep(0.1)
        peak["cur"] -= 1
        return "completed", "ok", None

    r._execute = tracked_execute  # type: ignore[assignment]

    async def _stop_soon():
        await asyncio.sleep(0.6)
        r.stop()

    await asyncio.gather(r.run(), _stop_soon())
    assert peak["max"] <= 2  # never more than max_concurrent in flight
    assert len(app.state.fleet_task_store._completed) == 4  # all drained


@pytest.mark.asyncio
async def test_run_drains_then_stops_on_signal_event(app_token):
    app, token = app_token
    r = _runner(app, token, heartbeat_interval=0.05)
    wid, _ = await r.register()
    await _approve(app, token, wid)
    app.state.fleet_task_store.enqueue({"run_id": "rA", "model": "gpt-4o", "pool": "default"})

    async def _stop_soon():
        await asyncio.sleep(0.3)
        r.stop()

    await asyncio.gather(r.run(), _stop_soon())
    assert app.state.fleet_task_store._completed.get("rA", {}).get("status") == "completed"


@pytest.mark.asyncio
async def test_exec_env_is_allowlisted_dropping_arbitrary_secrets(monkeypatch):
    # The --exec child env is default-DENY: ANY ambient secret (not just a hardcoded
    # denylist) must be dropped, while system vars (PATH) + SAGEWAI_TASK_* survive.
    monkeypatch.setenv("SAGEWAI_ADMIN_TOKEN", "secret-token")
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", "secret-master")
    monkeypatch.setenv("DATABASE_URL", "postgres://secret-db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-key")
    r = WorkerRunner(
        base_url="http://test",
        exec_cmd=(
            'printf "TOK=[%s] MK=[%s] DB=[%s] OAI=[%s] RID=[%s] HASPATH=[%s]" '
            '"$SAGEWAI_ADMIN_TOKEN" "$SAGEWAI_MASTER_KEY" "$DATABASE_URL" '
            '"$OPENAI_API_KEY" "$SAGEWAI_TASK_RUN_ID" "${PATH:+yes}"'
        ),
    )
    status, output, error = await r._execute({"run_id": "rX", "model": "m", "pool": "p"})
    assert status == "completed", (status, error, output)
    for secret in ("secret-token", "secret-master", "postgres://secret-db", "sk-secret-key"):
        assert secret not in output
    assert "TOK=[] MK=[] DB=[] OAI=[]" in output  # all secrets scrubbed → empty
    assert "RID=[rX]" in output  # task IDs still injected
    assert "HASPATH=[yes]" in output  # PATH preserved so the command can run


@pytest.mark.asyncio
async def test_claim_401_is_terminal(app_token):
    # A reused worker started with a bad/missing token gets 401 on claim — that is
    # terminal (must not loop forever / exit as "no task").
    app, token = app_token
    r = _runner(app, token, worker_id="w-any")  # skip register

    async def fake_post(url, *a, **kw):
        return httpx.Response(
            401, request=httpx.Request("POST", "http://test/api/v1/fleet/claim")
        )

    r.http_client.post = fake_post  # type: ignore[assignment]
    result = await r.run_once()
    assert result["claimed"] is False
    assert result["reason"] == "terminal"
    assert "SAGEWAI_ADMIN_TOKEN" in result.get("detail", "")
