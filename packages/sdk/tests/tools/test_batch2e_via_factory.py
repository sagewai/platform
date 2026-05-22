# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end: catalog -> factory -> executor -> builtin for batch-2e tools."""
import pytest
import respx

from sagewai.tools import factory, registry


def _shopify_creds(*, project_id, kind, id):
    return {
        "SHOPIFY_STORE": "teststore.myshopify.com",
        "SHOPIFY_ACCESS_TOKEN": "shpat-xyz",
    }


def _magento_creds(*, project_id, kind, id):
    return {
        "MAGENTO_TOKEN": "mag-tok",
        "MAGENTO_BASE_URL": "https://shop.example.com/rest",
    }


def _joor_creds(*, project_id, kind, id):
    return {"JOOR_API_KEY": "joor-key"}


@pytest.mark.asyncio
@respx.mock
async def test_shopify_list_products_via_factory():
    respx.post(
        "https://teststore.myshopify.com/admin/api/2025-10/graphql.json"
    ).respond(200, json={"data": {"products": {"edges": []}}})
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_shopify_creds)
    out = await callables["shopify"]({"_operation": "list_products"})
    assert out["data"]["products"]["edges"] == []


@pytest.mark.asyncio
@respx.mock
async def test_shopify_create_product_via_factory():
    route = respx.post(
        "https://teststore.myshopify.com/admin/api/2025-10/graphql.json"
    ).respond(200, json={"data": {"productCreate": {"product": {"id": "gid://shopify/Product/1"}}}})
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_shopify_creds)
    out = await callables["shopify"]({
        "_operation": "create_product",
        "input": {"title": "Widget"},
    })
    assert out["data"]["productCreate"]["product"]["id"] == "gid://shopify/Product/1"
    assert "CreateProduct" in route.calls.last.request.content.decode()


@pytest.mark.asyncio
@respx.mock
async def test_magento_get_product_via_factory():
    """runtime_base_url_field override + path placeholder substitution."""
    respx.get("https://shop.example.com/rest/V1/products/ABC-123").respond(
        200, json={"id": 42, "sku": "ABC-123", "name": "Widget"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_magento_creds)
    out = await callables["magento"]({"_operation": "get_product", "sku": "ABC-123"})
    assert out["sku"] == "ABC-123"


@pytest.mark.asyncio
@respx.mock
async def test_magento_create_customer_via_factory():
    route = respx.post("https://shop.example.com/rest/V1/customers").respond(
        200, json={"id": 7, "email": "buyer@example.com"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_magento_creds)
    out = await callables["magento"]({
        "_operation": "create_customer",
        "customer": {"email": "buyer@example.com", "firstname": "Sam", "lastname": "Buyer"},
    })
    assert out["id"] == 7
    assert route.calls.last.request.headers["Authorization"] == "Bearer mag-tok"


@pytest.mark.asyncio
@respx.mock
async def test_joor_list_products_via_factory():
    route = respx.get("https://api.joor.com/v3/products").respond(
        200, json={"data": [{"id": "p-1", "name": "Jacket"}]},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_joor_creds)
    out = await callables["joor_api"]({"_operation": "list_products"})
    assert out["data"][0]["id"] == "p-1"
    assert route.calls.last.request.headers["x-api-key"] == "joor-key"


@pytest.mark.asyncio
@respx.mock
async def test_joor_get_order_via_factory():
    respx.get("https://api.joor.com/v3/orders/o-99").respond(
        200, json={"data": {"id": "o-99", "status": "open"}},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_joor_creds)
    out = await callables["joor_api"]({"_operation": "get_order", "id": "o-99"})
    assert out["data"]["id"] == "o-99"


@pytest.mark.asyncio
@respx.mock
async def test_joor_create_order_via_factory():
    respx.post("https://api.joor.com/v3/orders").respond(
        200, json={"data": {"id": "o-100", "status": "draft"}},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_joor_creds)
    out = await callables["joor_api"]({
        "_operation": "create_order",
        "order": {"retailer_id": "r-1", "items": []},
    })
    assert out["data"]["id"] == "o-100"
