# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.nutritionix."""
import json

import pytest
import respx

from sagewai.tools.builtins import nutritionix as nx_mod


def _creds(app_id="id_abc", app_key="key_xyz"):
    def _get(*, project_id, kind, id):
        out = {}
        if app_id is not None:
            out["NUTRITIONIX_APP_ID"] = app_id
        if app_key is not None:
            out["NUTRITIONIX_APP_KEY"] = app_key
        return out
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_dual_headers_sent():
    route = respx.get("https://trackapi.nutritionix.com/v2/search/instant").respond(
        200, json={"common": [], "branded": []}
    )
    await nx_mod.nutritionix(
        {"_operation": "search_instant", "query": "banana"},
        project_id="p1",
        get_credentials=_creds(),
    )
    req = route.calls.last.request
    assert req.headers["x-app-id"] == "id_abc"
    assert req.headers["x-app-key"] == "key_xyz"


@pytest.mark.asyncio
@respx.mock
async def test_natural_nutrients_posts_json_body():
    route = respx.post("https://trackapi.nutritionix.com/v2/natural/nutrients").respond(
        200, json={"foods": []}
    )
    await nx_mod.nutritionix(
        {"_operation": "natural_nutrients", "query": "two eggs and toast"},
        project_id="p1",
        get_credentials=_creds(),
    )
    req = route.calls.last.request
    assert json.loads(req.content) == {"query": "two eggs and toast"}


@pytest.mark.asyncio
@respx.mock
async def test_search_item_passes_nix_item_id():
    route = respx.get("https://trackapi.nutritionix.com/v2/search/item").respond(
        200, json={"foods": []}
    )
    await nx_mod.nutritionix(
        {"_operation": "search_item", "nix_item_id": "abc123"},
        project_id="p1",
        get_credentials=_creds(),
    )
    assert route.calls.last.request.url.params["nix_item_id"] == "abc123"


@pytest.mark.asyncio
async def test_missing_app_id_raises():
    with pytest.raises(RuntimeError, match="NUTRITIONIX_APP_ID"):
        await nx_mod.nutritionix(
            {"_operation": "search_instant", "query": "x"},
            project_id="p1",
            get_credentials=_creds(app_id=None),
        )


@pytest.mark.asyncio
async def test_missing_app_key_raises():
    with pytest.raises(RuntimeError, match="NUTRITIONIX_APP_KEY"):
        await nx_mod.nutritionix(
            {"_operation": "search_instant", "query": "x"},
            project_id="p1",
            get_credentials=_creds(app_key=None),
        )


@pytest.mark.asyncio
async def test_unknown_operation_raises():
    with pytest.raises(ValueError, match="unknown operation"):
        await nx_mod.nutritionix(
            {"_operation": "delete"},
            project_id="p1",
            get_credentials=_creds(),
        )
