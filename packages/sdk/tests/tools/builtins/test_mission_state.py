# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.mission_state."""
import pytest

from sagewai.tools.builtins import mission_state as ms


class FakeMission:
    def __init__(self, mission_id: str):
        self.mission_id = mission_id
        self.step_results: list[tuple[str, dict]] = []
        self.progress_calls: list[tuple[float, str | None]] = []
        self.hitl_requests: list[tuple[str, str, dict | None]] = []
        self.events: list[tuple[str, dict]] = []

    def record_step_result(self, step_id: str, payload: dict) -> None:
        self.step_results.append((step_id, payload))

    def record_progress(self, progress: float, *, note: str | None = None) -> None:
        self.progress_calls.append((progress, note))

    def emit_hitl_request(self, request_id: str, *, reason: str, payload: dict | None = None) -> None:
        self.hitl_requests.append((request_id, reason, payload))

    def publish_event(self, topic: str, payload: dict) -> None:
        self.events.append((topic, payload))


@pytest.fixture
def fake_mission():
    m = FakeMission("m-1")
    ms.set_mission_resolver(lambda mid: m)
    yield m
    ms.set_mission_resolver(ms._default_resolver)


@pytest.mark.asyncio
async def test_record_result_writes_step(fake_mission):
    out = await ms.record_result({
        "mission_id": "m-1", "step_id": "step-A", "payload": {"k": "v"},
    })
    assert out["stored"] is True
    assert isinstance(out["timestamp"], str) and "T" in out["timestamp"]
    assert fake_mission.step_results == [("step-A", {"k": "v"})]


@pytest.mark.asyncio
async def test_progress_track_with_total(fake_mission):
    out = await ms.progress_track({
        "mission_id": "m-1", "units_done": 3, "units_total": 10, "note": "halfway-ish",
    })
    assert out == {"progress": 0.3, "note": "halfway-ish"}
    assert fake_mission.progress_calls == [(0.3, "halfway-ish")]


@pytest.mark.asyncio
async def test_progress_track_without_total_returns_zero(fake_mission):
    out = await ms.progress_track({"mission_id": "m-1", "units_done": 5})
    assert out["progress"] == 0.0
    assert out["note"] is None


@pytest.mark.asyncio
async def test_request_approval_returns_request_id(fake_mission):
    out = await ms.request_approval({
        "mission_id": "m-1", "reason": "needs human sign-off", "payload": {"row_id": 42},
    })
    assert out["status"] == "pending"
    assert isinstance(out["request_id"], str) and len(out["request_id"]) > 0
    assert fake_mission.hitl_requests == [(out["request_id"], "needs human sign-off", {"row_id": 42})]


@pytest.mark.asyncio
async def test_default_resolver_raises():
    ms.set_mission_resolver(ms._default_resolver)
    with pytest.raises(ms.MissionNotBoundError):
        await ms.record_result({"mission_id": "m-99", "step_id": "x", "payload": {}})
