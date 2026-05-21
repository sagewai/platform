# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.maps."""
import pytest
import respx

from sagewai.tools.builtins import maps as maps_mod


def _creds(key: str = "AIza-test-key"):
    def _get(*, project_id, kind, id):
        return {"MAPS_API_KEY": key}
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_maps_route_happy_path():
    route = respx.get("https://maps.googleapis.com/maps/api/directions/json").respond(
        200,
        json={
            "status": "OK",
            "routes": [{
                "summary": "B96",
                "legs": [{"distance": {"text": "10 km"}, "duration": {"text": "20 mins"}}],
            }],
        },
    )
    out = await maps_mod.maps_route(
        {"origin": "Berlin", "destination": "Potsdam"},
        project_id="p1", get_credentials=_creds("AIza-real"),
    )
    assert out["status"] == "OK"
    assert len(out["routes"]) == 1
    req_url = str(route.calls.last.request.url)
    assert "origin=Berlin" in req_url
    assert "destination=Potsdam" in req_url
    assert "mode=driving" in req_url
    assert "key=AIza-real" in req_url


@pytest.mark.asyncio
@respx.mock
async def test_maps_route_with_waypoints():
    respx.get("https://maps.googleapis.com/maps/api/directions/json").respond(
        200, json={"status": "OK", "routes": []},
    )
    await maps_mod.maps_route(
        {
            "origin": "A", "destination": "D",
            "waypoints": ["B", "C"],
        },
        project_id="p1", get_credentials=_creds(),
    )
    req_url = str(respx.calls.last.request.url)
    # Waypoints joined with `|` (URL-encoded as %7C)
    assert "waypoints=B%7CC" in req_url or "waypoints=B|C" in req_url


@pytest.mark.asyncio
@respx.mock
async def test_maps_route_mode_override():
    respx.get("https://maps.googleapis.com/maps/api/directions/json").respond(
        200, json={"status": "OK", "routes": []},
    )
    await maps_mod.maps_route(
        {"origin": "A", "destination": "B", "mode": "walking"},
        project_id="p1", get_credentials=_creds(),
    )
    req_url = str(respx.calls.last.request.url)
    assert "mode=walking" in req_url


@pytest.mark.asyncio
async def test_maps_route_missing_key_raises():
    def _empty(*, project_id, kind, id):
        return {}
    with pytest.raises(RuntimeError, match="MAPS_API_KEY"):
        await maps_mod.maps_route(
            {"origin": "A", "destination": "B"},
            project_id="p1", get_credentials=_empty,
        )
