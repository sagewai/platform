# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Shopify tool — commerce operations via the GraphQL Admin API.

Shopify exposes a single GraphQL endpoint at
``POST https://{store}/admin/api/{version}/graphql.json``. Auth is the
``X-Shopify-Access-Token`` header. The store domain and access token are
per-operator, provided via the ``SHOPIFY_STORE`` and
``SHOPIFY_ACCESS_TOKEN`` credentials.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_DEFAULT_API_VERSION = "2025-10"

_QUERIES: dict[str, str] = {
    "list_products": """
        query ListProducts($first: Int, $query: String) {
            products(first: $first, query: $query) {
                edges { node { id title handle status } }
            }
        }
    """,
    "list_orders": """
        query ListOrders($first: Int, $query: String) {
            orders(first: $first, query: $query) {
                edges { node { id name displayFinancialStatus } }
            }
        }
    """,
    "get_order": """
        query GetOrder($id: ID!) {
            order(id: $id) { id name displayFinancialStatus }
        }
    """,
    "create_product": """
        mutation CreateProduct($input: ProductInput!) {
            productCreate(input: $input) {
                product { id title }
                userErrors { field message }
            }
        }
    """,
    "create_draft_order": """
        mutation CreateDraftOrder($input: DraftOrderInput!) {
            draftOrderCreate(input: $input) {
                draftOrder { id name }
                userErrors { field message }
            }
        }
    """,
    "adjust_inventory": """
        mutation AdjustInventory($input: InventoryAdjustQuantitiesInput!) {
            inventoryAdjustQuantities(input: $input) {
                inventoryAdjustmentGroup { createdAt }
                userErrors { field message }
            }
        }
    """,
}


def _variables_for(op: str, payload: dict[str, Any]) -> dict[str, Any]:
    if op in ("list_products", "list_orders"):
        return {"first": payload.get("first", 10), "query": payload.get("query")}
    if op == "get_order":
        return {"id": payload["id"]}
    if op in ("create_product", "create_draft_order", "adjust_inventory"):
        return {"input": payload.get("input", {})}
    raise ValueError(f"unknown operation: {op!r}")


async def shopify(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch a Shopify GraphQL Admin API operation."""
    op = payload.get("_operation")
    if op not in _QUERIES:
        raise ValueError(
            f"unknown operation: {op!r}; expected one of {list(_QUERIES)}"
        )

    creds = get_credentials(project_id=project_id, kind="tool", id="shopify")
    store = creds.get("SHOPIFY_STORE")
    if not store:
        raise RuntimeError("shopify: missing SHOPIFY_STORE credential")
    token = creds.get("SHOPIFY_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("shopify: missing SHOPIFY_ACCESS_TOKEN credential")
    version = creds.get("SHOPIFY_API_VERSION") or _DEFAULT_API_VERSION

    store = store.removeprefix("https://").removeprefix("http://").rstrip("/")
    url = f"https://{store}/admin/api/{version}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {"query": _QUERIES[op], "variables": _variables_for(op, payload)}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=body)
    resp.raise_for_status()
    result = resp.json()
    if result.get("errors"):
        raise RuntimeError(f"shopify: GraphQL errors: {result['errors']}")
    return result
