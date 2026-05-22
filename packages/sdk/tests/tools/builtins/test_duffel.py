# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.duffel."""
import json

import pytest
import respx

from sagewai.tools.builtins import duffel as du_mod


def _creds(token="duffel_test_abc"):
    def _get(*, project_id, kind, id):
        return {"DUFFEL_ACCESS_TOKEN": token}
    return _get


_SLICE = {"origin": "LHR", "destination": "JFK", "departure_date": "2026-07-01"}
_PAX = [{"type": "adult"}]


@pytest.mark.asyncio
@respx.mock
async def test_bearer_and_version_headers_sent():
    route = respx.post("https://api.duffel.com/air/offer_requests").respond(
        201, json={"data": {"id": "orq-1", "offers": []}},
    )
    await du_mod.duffel(
        {"_operation": "search_flights", "slices": [_SLICE], "passengers": _PAX},
        project_id="p1", get_credentials=_creds(),
    )
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer duffel_test_abc"
    assert req.headers["Duffel-Version"] == "v2"


@pytest.mark.asyncio
@respx.mock
async def test_search_flights_wraps_data_and_sets_return_offers():
    route = respx.post("https://api.duffel.com/air/offer_requests").respond(
        201, json={"data": {"id": "orq-1", "offers": []}},
    )
    await du_mod.duffel(
        {"_operation": "search_flights", "slices": [_SLICE],
         "passengers": _PAX, "cabin_class": "economy"},
        project_id="p1", get_credentials=_creds(),
    )
    req = route.calls.last.request
    assert req.url.params["return_offers"] == "true"
    body = json.loads(req.content)
    assert body["data"]["slices"] == [_SLICE]
    assert body["data"]["cabin_class"] == "economy"


@pytest.mark.asyncio
@respx.mock
async def test_list_offers_sends_offer_request_id_query():
    route = respx.get("https://api.duffel.com/air/offers").respond(
        200, json={"data": []},
    )
    await du_mod.duffel(
        {"_operation": "list_offers", "offer_request_id": "orq-9"},
        project_id="p1", get_credentials=_creds(),
    )
    assert route.calls.last.request.url.params["offer_request_id"] == "orq-9"


@pytest.mark.asyncio
@respx.mock
async def test_get_offer_substitutes_path_id():
    route = respx.get("https://api.duffel.com/air/offers/off-77").respond(
        200, json={"data": {"id": "off-77"}},
    )
    out = await du_mod.duffel(
        {"_operation": "get_offer", "id": "off-77"},
        project_id="p1", get_credentials=_creds(),
    )
    assert out["data"]["id"] == "off-77"
    assert route.called


@pytest.mark.asyncio
async def test_missing_token_raises():
    def _get(*, project_id, kind, id):
        return {}
    with pytest.raises(RuntimeError, match="DUFFEL_ACCESS_TOKEN"):
        await du_mod.duffel(
            {"_operation": "list_offers", "offer_request_id": "x"},
            project_id="p1", get_credentials=_get,
        )


@pytest.mark.asyncio
async def test_unknown_operation_raises():
    with pytest.raises(ValueError, match="unknown operation"):
        await du_mod.duffel(
            {"_operation": "book_flight"},
            project_id="p1", get_credentials=_creds(),
        )
