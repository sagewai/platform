# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.usda_fdc."""
import pytest
import respx

from sagewai.tools.builtins import usda_fdc as fdc_mod


def _creds(key="DEMO_KEY"):
    def _get(*, project_id, kind, id):
        return {} if key is None else {"USDA_FDC_API_KEY": key}
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_search_foods_sends_api_key_and_query():
    route = respx.get("https://api.nal.usda.gov/fdc/v1/foods/search").respond(
        200, json={"foods": []}
    )
    await fdc_mod.usda_fdc(
        {"_operation": "search_foods", "query": "cheddar", "pageSize": 5},
        project_id="p1",
        get_credentials=_creds(),
    )
    p = route.calls.last.request.url.params
    assert p["api_key"] == "DEMO_KEY"
    assert p["query"] == "cheddar"
    assert p["pageSize"] == "5"


@pytest.mark.asyncio
@respx.mock
async def test_get_food_substitutes_fdc_id():
    route = respx.get("https://api.nal.usda.gov/fdc/v1/food/12345").respond(
        200, json={"fdcId": 12345}
    )
    await fdc_mod.usda_fdc(
        {"_operation": "get_food", "fdcId": "12345"},
        project_id="p1",
        get_credentials=_creds(),
    )
    assert route.called
    assert route.calls.last.request.url.params["api_key"] == "DEMO_KEY"


@pytest.mark.asyncio
@respx.mock
async def test_list_foods_optional_params():
    route = respx.get("https://api.nal.usda.gov/fdc/v1/foods/list").respond(
        200, json=[]
    )
    await fdc_mod.usda_fdc(
        {"_operation": "list_foods", "dataType": "Branded", "pageSize": 10},
        project_id="p1",
        get_credentials=_creds(),
    )
    p = route.calls.last.request.url.params
    assert p["dataType"] == "Branded"
    assert p["pageSize"] == "10"


@pytest.mark.asyncio
async def test_missing_key_raises():
    with pytest.raises(RuntimeError, match="USDA_FDC_API_KEY"):
        await fdc_mod.usda_fdc(
            {"_operation": "search_foods", "query": "x"},
            project_id="p1",
            get_credentials=_creds(key=None),
        )


@pytest.mark.asyncio
async def test_unknown_operation_raises():
    with pytest.raises(ValueError, match="unknown operation"):
        await fdc_mod.usda_fdc(
            {"_operation": "delete"},
            project_id="p1",
            get_credentials=_creds(),
        )
