# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end: catalog -> factory -> executor -> builtin for batch-2g tools."""
import pytest
import respx

from sagewai.tools import factory, registry


def _terra_creds(*, project_id, kind, id):
    return {
        "TERRA_API_KEY": "tk_terra_test",
        "TERRA_DEV_ID": "dev_terra_xyz",
    }


def _vital_creds(*, project_id, kind, id):
    # VITAL_API_KEY must come first so the api_key auth picker finds it
    return {
        "VITAL_API_KEY": "vt-key-sandbox",
    }


def _vital_creds_with_override(*, project_id, kind, id):
    return {
        "VITAL_API_KEY": "vt-key-prod",
        "VITAL_BASE_URL": "https://api.tryvital.io/prod",
    }


def _nutritionix_creds(*, project_id, kind, id):
    return {
        "NUTRITIONIX_APP_ID": "nx-app-id",
        "NUTRITIONIX_APP_KEY": "nx-app-key",
    }


def _usda_fdc_creds(*, project_id, kind, id):
    return {"USDA_FDC_API_KEY": "DEMO_KEY"}


def _openfda_creds(*, project_id, kind, id):
    return {"OPENFDA_API_KEY": "fda-key-123"}


def _rxnorm_creds(*, project_id, kind, id):
    # RxNorm has no auth; return empty dict
    return {}


def _infermedica_creds(*, project_id, kind, id):
    return {
        "INFERMEDICA_APP_ID": "inf-app-id",
        "INFERMEDICA_APP_KEY": "inf-app-key",
    }


@pytest.mark.asyncio
@respx.mock
async def test_terra_api_dual_headers_via_factory():
    route = respx.get("https://api.tryterra.co/v2/userInfo").respond(
        200, json={"users": []}
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_terra_creds)
    out = await callables["terra_api"]({"_operation": "list_users"})
    assert out == {"users": []}
    req = route.calls.last.request
    assert req.headers["x-api-key"] == "tk_terra_test"
    assert req.headers["dev-id"] == "dev_terra_xyz"


@pytest.mark.asyncio
@respx.mock
async def test_vital_api_auth_header_via_factory():
    route = respx.get("https://api.tryvital.io/v2/user").respond(
        200, json={"users": []}
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_vital_creds)
    out = await callables["vital_api"]({"_operation": "list_users"})
    assert out == {"users": []}
    assert route.calls.last.request.headers["x-vital-api-key"] == "vt-key-sandbox"


@pytest.mark.asyncio
@respx.mock
async def test_vital_api_runtime_base_url_override_via_factory():
    """VITAL_BASE_URL credential overrides the default base URL."""
    route = respx.get("https://api.tryvital.io/prod/v2/user").respond(
        200, json={"users": []}
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(
        project_id="p1", get_credentials=_vital_creds_with_override
    )
    out = await callables["vital_api"]({"_operation": "list_users"})
    assert out == {"users": []}
    assert route.calls.last.request.headers["x-vital-api-key"] == "vt-key-prod"


@pytest.mark.asyncio
@respx.mock
async def test_nutritionix_api_dual_headers_via_factory():
    route = respx.get(
        "https://trackapi.nutritionix.com/v2/search/instant"
    ).respond(200, json={"common": [], "branded": []})
    registry._reset()
    registry.load()
    callables = factory.build_callables(
        project_id="p1", get_credentials=_nutritionix_creds
    )
    out = await callables["nutritionix_api"](
        {"_operation": "search_instant", "query": "apple"}
    )
    assert out == {"common": [], "branded": []}
    req = route.calls.last.request
    assert req.headers["x-app-id"] == "nx-app-id"
    assert req.headers["x-app-key"] == "nx-app-key"


@pytest.mark.asyncio
@respx.mock
async def test_usda_fdc_api_query_param_via_factory():
    route = respx.get("https://api.nal.usda.gov/fdc/v1/foods/search").respond(
        200, json={"foods": [{"fdcId": 1, "description": "Cheddar"}]}
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(
        project_id="p1", get_credentials=_usda_fdc_creds
    )
    out = await callables["usda_fdc_api"](
        {"_operation": "search_foods", "query": "cheddar"}
    )
    assert out["foods"][0]["fdcId"] == 1
    assert route.calls.last.request.url.params["api_key"] == "DEMO_KEY"


@pytest.mark.asyncio
@respx.mock
async def test_openfda_api_query_param_via_factory():
    route = respx.get("https://api.fda.gov/drug/event.json").respond(
        200, json={"results": [{"safetyreportid": "r-1"}]}
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(
        project_id="p1", get_credentials=_openfda_creds
    )
    out = await callables["openfda_api"](
        {"_operation": "drug_event", "search": 'receivedate:[20260101+TO+20260201]'}
    )
    assert out["results"][0]["safetyreportid"] == "r-1"
    assert route.calls.last.request.url.params["api_key"] == "fda-key-123"


@pytest.mark.asyncio
@respx.mock
async def test_rxnorm_api_no_auth_via_factory():
    route = respx.get("https://rxnav.nlm.nih.gov/REST/rxcui.json").respond(
        200, json={"idGroup": {"rxnormId": ["123456"]}}
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(
        project_id="p1", get_credentials=_rxnorm_creds
    )
    out = await callables["rxnorm_api"]({"_operation": "get_rxcui", "name": "ibuprofen"})
    assert out["idGroup"]["rxnormId"] == ["123456"]
    req = route.calls.last.request
    # no auth headers should be added
    assert "Authorization" not in req.headers
    assert "x-api-key" not in req.headers
    assert "apikey" not in req.headers


@pytest.mark.asyncio
@respx.mock
async def test_infermedica_api_dual_headers_via_factory():
    route = respx.post("https://api.infermedica.com/v3/parse").respond(
        200, json={"mentions": [{"id": "s_21", "name": "Headache"}]}
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(
        project_id="p1", get_credentials=_infermedica_creds
    )
    out = await callables["infermedica_api"]({
        "_operation": "parse_symptoms",
        "text": "I have a headache",
        "age": {"value": 30},
    })
    assert out["mentions"][0]["id"] == "s_21"
    req = route.calls.last.request
    assert req.headers["App-Id"] == "inf-app-id"
    assert req.headers["App-Key"] == "inf-app-key"
