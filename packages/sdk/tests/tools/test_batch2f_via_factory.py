# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end: catalog -> factory -> executor -> builtin for batch-2f tools."""
import pytest
import respx

from sagewai.tools import factory, registry


def _duffel_creds(*, project_id, kind, id):
    return {"DUFFEL_ACCESS_TOKEN": "duffel_test_abc"}


def _liteapi_creds(*, project_id, kind, id):
    return {"LITEAPI_KEY": "lite-key"}


def _transitland_creds(*, project_id, kind, id):
    return {"TRANSITLAND_API_KEY": "tl-key"}


def _marinetraffic_creds(*, project_id, kind, id):
    return {"MARINETRAFFIC_API_KEY": "mt-key"}


@pytest.mark.asyncio
@respx.mock
async def test_duffel_search_flights_via_factory():
    respx.post("https://api.duffel.com/air/offer_requests").respond(
        201, json={"data": {"id": "orq-1", "offers": []}},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_duffel_creds)
    out = await callables["duffel_api"]({
        "_operation": "search_flights",
        "slices": [{"origin": "LHR", "destination": "JFK", "departure_date": "2026-07-01"}],
        "passengers": [{"type": "adult"}],
    })
    assert out["data"]["id"] == "orq-1"


@pytest.mark.asyncio
@respx.mock
async def test_liteapi_search_hotels_via_factory():
    route = respx.get("https://api.liteapi.travel/v3.0/data/hotels").respond(
        200, json={"data": [{"id": "htl-1", "name": "Grand"}]},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_liteapi_creds)
    out = await callables["liteapi"]({"_operation": "search_hotels", "countryCode": "FR"})
    assert out["data"][0]["id"] == "htl-1"
    assert route.calls.last.request.headers["X-API-Key"] == "lite-key"


@pytest.mark.asyncio
@respx.mock
async def test_liteapi_get_rates_via_factory():
    respx.post("https://api.liteapi.travel/v3.0/hotels/rates").respond(
        200, json={"data": [{"hotelId": "htl-1"}]},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_liteapi_creds)
    out = await callables["liteapi"]({
        "_operation": "get_rates",
        "hotelIds": ["htl-1"],
        "checkin": "2026-07-01",
        "checkout": "2026-07-03",
        "occupancies": [{"adults": 2}],
    })
    assert out["data"][0]["hotelId"] == "htl-1"


@pytest.mark.asyncio
@respx.mock
async def test_transitland_search_stops_via_factory():
    route = respx.get("https://transit.land/api/v2/rest/stops").respond(
        200, json={"stops": [{"onestop_id": "s-1"}]},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_transitland_creds)
    out = await callables["transitland_api"]({"_operation": "search_stops"})
    assert out["stops"][0]["onestop_id"] == "s-1"
    assert route.calls.last.request.headers["apikey"] == "tl-key"


@pytest.mark.asyncio
@respx.mock
async def test_transitland_get_stop_departures_via_factory():
    """Path placeholder {stop_key} substitution."""
    respx.get("https://transit.land/api/v2/rest/stops/s-onestop-1/departures").respond(
        200, json={"stops": [{"departures": []}]},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_transitland_creds)
    out = await callables["transitland_api"]({
        "_operation": "get_stop_departures",
        "stop_key": "s-onestop-1",
    })
    assert "stops" in out


@pytest.mark.asyncio
@respx.mock
async def test_marinetraffic_vessel_positions_via_factory():
    url = "https://services.marinetraffic.com/api/exportvessel/v:8/mt-key/protocol:jsono"
    respx.get(url).respond(200, json=[{"MMSI": 999}])
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_marinetraffic_creds)
    out = await callables["marinetraffic_api"]({"_operation": "vessel_positions"})
    assert out["data"][0]["MMSI"] == 999


@pytest.mark.asyncio
@respx.mock
async def test_marinetraffic_port_calls_via_factory():
    url = "https://services.marinetraffic.com/api/portcalls/v:5/mt-key/protocol:jsono"
    respx.get(url).respond(200, json=[])
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_marinetraffic_creds)
    out = await callables["marinetraffic_api"]({"_operation": "port_calls"})
    assert out["data"] == []
