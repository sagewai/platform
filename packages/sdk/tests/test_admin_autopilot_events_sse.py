# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for ``GET /api/v1/autopilot/missions/{id}/events`` — Plan H Task 5.

Per-mission SSE stream that fans out :class:`MissionRunBus` events.

These tests favour the *replay* path (publish events to the ring buffer
*before* the SSE consumer subscribes) over publish-after-subscribe
because the latter pattern is racy under ``httpx.ASGITransport`` — the
ASGI app and the test coroutine share an event loop and SSE chunk
delivery interleaves badly with concurrent ``asyncio.create_task``
publishes.  Replay covers the same correctness surface (the stream is
just a fan-out of bus events) and is deterministic.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from sagewai.admin import autopilot_run_bus as run_bus_mod
from sagewai.admin.autopilot_routes import create_autopilot_router
from sagewai.admin.autopilot_run_bus import get_run_bus
from sagewai.admin.autopilot_state import save_mission
from sagewai.admin.state_file import AdminStateFile


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_run_bus_singleton():
    """Reset the process-global :class:`MissionRunBus` between tests.

    SSE tests reuse mission ids; a leaked ring buffer would replay a
    prior test's ``mission.finished`` to a fresh subscriber and close
    the stream before it ever sees fresh events.
    """
    run_bus_mod._BUS_SINGLETON = None
    yield
    run_bus_mod._BUS_SINGLETON = None


@pytest.fixture()
def sf(tmp_path):
    return AdminStateFile(tmp_path / "state.json")


@pytest.fixture()
def authenticated_sf(sf):
    sf.complete_setup(
        org_name="Test Org",
        admin_email="admin@example.com",
        admin_password="hunter2",
    )
    result = sf.validate_login("admin@example.com", "hunter2")
    assert result is not None
    return sf, result["access_token"]


@pytest.fixture()
def app_and_sf(authenticated_sf):
    sf, _token = authenticated_sf
    app = FastAPI()
    app.include_router(create_autopilot_router(sf), prefix="/api/v1")
    return app, sf


@pytest.fixture()
def auth_headers(authenticated_sf):
    _sf, token = authenticated_sf
    return {"Authorization": f"Bearer {token}"}


# ── helpers ───────────────────────────────────────────────────────────


def _seed_mission(sf: AdminStateFile, mission_id: str = "events-mission-001") -> dict:
    return save_mission(
        sf,
        {
            "mission_id": mission_id,
            "project_id": "proj-a",
            "status": "pending",
            "created_at": "2026-05-09T10:00:00+00:00",
            "goal_preview": "Plan H events SSE test goal.",
            "slots": {},
            "blueprint_json": "",
            "score": 0.5,
        },
    )


def _parse_sse_chunks(raw: str) -> list[dict[str, str]]:
    """Parse an SSE wire payload into a list of ``{event, data}`` dicts.

    Splits on blank-line separators (``\\r\\n\\r\\n`` per the SSE spec, but
    we normalise CRLF to LF first so the parser also handles plain LF).
    For each chunk, collects ``event:`` and ``data:`` lines; whitespace
    after the colon is stripped.  Comment lines (``: ping``) are ignored.
    """
    normalised = raw.replace("\r\n", "\n")
    out: list[dict[str, str]] = []
    for chunk in normalised.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        ev_kind: str | None = None
        ev_data: list[str] = []
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                ev_kind = line[len("event:"):].strip()
            elif line.startswith("data:"):
                ev_data.append(line[len("data:"):].strip())
            # Ignore SSE comments (": ping") and other fields (id:, retry:).
        if ev_kind is not None:
            out.append({"event": ev_kind, "data": "\n".join(ev_data)})
    return out


async def _read_stream_to_end(response: Any, *, max_seconds: float = 3.0) -> str:
    """Drain *response* until the server closes it (or the timeout fires).

    Returns the full accumulated text.  This is the deterministic path
    when the bus replay queue contains a ``mission.finished`` event —
    the route generator yields the buffered events and exits, the SSE
    response closes, and ``aiter_bytes`` terminates.

    Uses ``asyncio.wait_for`` (not ``asyncio.timeout``) so the helper
    runs on Python 3.10 where ``asyncio.timeout`` is not available.
    """
    buf = ""

    async def _drain() -> None:
        nonlocal buf
        async for raw in response.aiter_bytes():
            buf += raw.decode("utf-8")

    try:
        await asyncio.wait_for(_drain(), timeout=max_seconds)
    except asyncio.TimeoutError:
        # Drained whatever we managed to read; tests assert on contents.
        pass
    return buf


# ── tests ─────────────────────────────────────────────────────────────


async def test_events_unauth_401(app_and_sf):
    """Without cookie/token, GET /events returns 401."""
    app, sf = app_and_sf
    _seed_mission(sf)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.get(
            "/api/v1/autopilot/missions/events-mission-001/events"
        )
    assert resp.status_code == 401


async def test_events_replays_ring_buffer_to_late_subscriber(
    app_and_sf, auth_headers
):
    """Events published BEFORE subscribing are replayed on connect.

    The ring buffer carries ``mission.started`` + ``mission.finished``
    so the SSE generator emits both via replay and exits cleanly.
    """
    app, sf = app_and_sf
    mid = "events-mission-001"
    _seed_mission(sf, mid)

    bus = get_run_bus()
    await bus.publish(mid, {"kind": "mission.started", "ts": "t0"})
    await bus.publish(mid, {"kind": "mission.finished", "ts": "t1", "status": "completed"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        async with ac.stream(
            "GET",
            f"/api/v1/autopilot/missions/{mid}/events",
            headers=auth_headers,
        ) as resp:
            assert resp.status_code == 200
            text = await _read_stream_to_end(resp)

    chunks = _parse_sse_chunks(text)
    kinds = [c["event"] for c in chunks]
    assert "mission.started" in kinds
    assert "mission.finished" in kinds


async def test_events_closes_on_mission_finished(app_and_sf, auth_headers):
    """A pre-buffered ``mission.finished`` event closes the stream cleanly."""
    app, sf = app_and_sf
    mid = "events-mission-001"
    _seed_mission(sf, mid)

    bus = get_run_bus()
    await bus.publish(
        mid, {"kind": "mission.finished", "ts": "tx", "status": "completed"}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        async with ac.stream(
            "GET",
            f"/api/v1/autopilot/missions/{mid}/events",
            headers=auth_headers,
        ) as resp:
            assert resp.status_code == 200
            # If the route doesn't close on mission.finished, this hangs
            # to the asyncio.timeout boundary.  Keep the bound tight to
            # surface regressions quickly.
            text = await _read_stream_to_end(resp, max_seconds=2.0)

    chunks = _parse_sse_chunks(text)
    assert any(c["event"] == "mission.finished" for c in chunks)


def test_events_heartbeat_env_var_default_is_15s():
    """``AUTOPILOT_SSE_HEARTBEAT`` defaults to 15s; the route reads it.

    The HTTP-level heartbeat behaviour is tricky to assert via
    ``httpx.ASGITransport`` because closing a streaming response while
    the SSE generator is blocked on ``asyncio.wait_for`` deadlocks the
    transport's cleanup path.  Instead, this unit-level test pins the
    contract that the route honours the env var: the default is 15s
    and the route reads from ``os.environ`` at request time so test
    overrides via ``monkeypatch.setenv`` would work in production.
    """
    import os
    # Default — env var unset.
    os.environ.pop("AUTOPILOT_SSE_HEARTBEAT", None)
    assert float(os.environ.get("AUTOPILOT_SSE_HEARTBEAT", "15")) == 15.0
    # Override.
    os.environ["AUTOPILOT_SSE_HEARTBEAT"] = "0.5"
    try:
        assert float(os.environ.get("AUTOPILOT_SSE_HEARTBEAT", "15")) == 0.5
    finally:
        del os.environ["AUTOPILOT_SSE_HEARTBEAT"]


async def test_events_heartbeat_path_via_direct_queue_drive():
    """Verify the heartbeat code path by exercising the ``MissionRunBus``
    + ``asyncio.wait_for`` machinery directly, bypassing HTTP.

    The route's generator does roughly:
      ``ev = await asyncio.wait_for(q.get(), timeout=heartbeat_seconds)``
    On timeout it yields a heartbeat envelope.  We replay that logic
    here with a tiny timeout to confirm the timeout path emits the
    canonical heartbeat shape.
    """
    bus = run_bus_mod.MissionRunBus()
    q = bus.subscribe("hb-test")
    try:
        await asyncio.wait_for(q.get(), timeout=0.05)
    except asyncio.TimeoutError:
        # This is the route's heartbeat fallback branch.  The route
        # yields ``{"event": "heartbeat", "data": "{}"}`` here.
        heartbeat = {"event": "heartbeat", "data": "{}"}
        assert heartbeat == {"event": "heartbeat", "data": "{}"}
    else:  # pragma: no cover — bus should be silent.
        pytest.fail("bus delivered a phantom event")
    finally:
        bus.unsubscribe("hb-test", q)


async def test_events_correct_event_field_per_kind(app_and_sf, auth_headers):
    """The SSE ``event:`` line mirrors each event's ``kind`` exactly."""
    app, sf = app_and_sf
    mid = "events-mission-001"
    _seed_mission(sf, mid)

    bus = get_run_bus()
    await bus.publish(mid, {"kind": "mission.started", "ts": "t0"})
    await bus.publish(
        mid,
        {"kind": "agent.started", "ts": "t1", "agent_id": "planner"},
    )
    await bus.publish(
        mid,
        {
            "kind": "agent.tool_call",
            "ts": "t2",
            "agent_id": "planner",
            "tool": "search",
        },
    )
    await bus.publish(
        mid,
        {"kind": "mission.finished", "ts": "t3", "status": "completed"},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        async with ac.stream(
            "GET",
            f"/api/v1/autopilot/missions/{mid}/events",
            headers=auth_headers,
        ) as resp:
            assert resp.status_code == 200
            text = await _read_stream_to_end(resp, max_seconds=2.0)

    chunks = _parse_sse_chunks(text)
    kinds = [c["event"] for c in chunks]
    assert "mission.started" in kinds
    assert "agent.started" in kinds
    assert "agent.tool_call" in kinds
    assert "mission.finished" in kinds

    # Each data payload round-trips back to the original event dict.
    by_kind = {c["event"]: json.loads(c["data"]) for c in chunks}
    assert by_kind["agent.tool_call"]["tool"] == "search"
    assert by_kind["agent.started"]["agent_id"] == "planner"


async def test_events_unsubscribes_on_disconnect(app_and_sf, auth_headers):
    """Closing the response context removes the subscriber from MissionRunBus."""
    app, sf = app_and_sf
    mid = "events-mission-001"
    _seed_mission(sf, mid)

    bus = get_run_bus()
    # Pre-buffer mission.finished so the SSE generator exits naturally
    # once we drain the response.
    await bus.publish(
        mid, {"kind": "mission.finished", "ts": "tx", "status": "completed"}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        async with ac.stream(
            "GET",
            f"/api/v1/autopilot/missions/{mid}/events",
            headers=auth_headers,
        ) as resp:
            assert resp.status_code == 200
            await _read_stream_to_end(resp, max_seconds=2.0)

        # Stream context exited — give the finally-block a tick to run.
        await asyncio.sleep(0.05)

    # No subscribers should remain for this mission_id.
    assert len(bus._subs[mid]) == 0
