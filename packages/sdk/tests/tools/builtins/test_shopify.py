# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.shopify."""
import pytest
import respx

from sagewai.tools.builtins import shopify as sh_mod


def _creds(store="teststore.myshopify.com", token="shpat-xyz", version=None):
    def _get(*, project_id, kind, id):
        c = {"SHOPIFY_STORE": store, "SHOPIFY_ACCESS_TOKEN": token}
        if version is not None:
            c["SHOPIFY_API_VERSION"] = version
        return c
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_token_sent_as_shopify_header():
    route = respx.post(
        "https://teststore.myshopify.com/admin/api/2025-10/graphql.json"
    ).respond(200, json={"data": {"products": {"edges": []}}})
    await sh_mod.shopify(
        {"_operation": "list_products"},
        project_id="p1", get_credentials=_creds(),
    )
    req = route.calls.last.request
    assert req.headers["X-Shopify-Access-Token"] == "shpat-xyz"
    assert "Authorization" not in req.headers


@pytest.mark.asyncio
@respx.mock
async def test_url_uses_default_api_version():
    route = respx.post(
        "https://teststore.myshopify.com/admin/api/2025-10/graphql.json"
    ).respond(200, json={"data": {}})
    await sh_mod.shopify(
        {"_operation": "list_products"},
        project_id="p1", get_credentials=_creds(),
    )
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_api_version_override_honored():
    route = respx.post(
        "https://teststore.myshopify.com/admin/api/2024-04/graphql.json"
    ).respond(200, json={"data": {}})
    await sh_mod.shopify(
        {"_operation": "list_products"},
        project_id="p1", get_credentials=_creds(version="2024-04"),
    )
    assert route.called


@pytest.mark.asyncio
async def test_missing_store_raises():
    def _get(*, project_id, kind, id):
        return {"SHOPIFY_ACCESS_TOKEN": "t"}
    with pytest.raises(RuntimeError, match="SHOPIFY_STORE"):
        await sh_mod.shopify(
            {"_operation": "list_products"},
            project_id="p1", get_credentials=_get,
        )


@pytest.mark.asyncio
@respx.mock
async def test_graphql_errors_array_raises():
    respx.post(
        "https://teststore.myshopify.com/admin/api/2025-10/graphql.json"
    ).respond(200, json={"errors": [{"message": "Field 'bogus' doesn't exist"}]})
    with pytest.raises(RuntimeError, match="GraphQL errors"):
        await sh_mod.shopify(
            {"_operation": "list_products"},
            project_id="p1", get_credentials=_creds(),
        )


@pytest.mark.asyncio
@respx.mock
async def test_query_and_mutation_post_correct_body():
    route = respx.post(
        "https://teststore.myshopify.com/admin/api/2025-10/graphql.json"
    ).respond(200, json={"data": {}})
    await sh_mod.shopify(
        {"_operation": "list_products", "query": "status:active"},
        project_id="p1", get_credentials=_creds(),
    )
    body = route.calls.last.request.content.decode()
    assert "ListProducts" in body
    assert "status:active" in body

    await sh_mod.shopify(
        {"_operation": "create_product", "input": {"title": "New Widget"}},
        project_id="p1", get_credentials=_creds(),
    )
    body = route.calls.last.request.content.decode()
    assert "CreateProduct" in body
    assert "New Widget" in body
