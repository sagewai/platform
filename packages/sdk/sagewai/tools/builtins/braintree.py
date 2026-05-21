# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Braintree tool — payment processing via GraphQL.

Braintree's modern API is a single ``POST /graphql`` endpoint. Auth is
HTTP Basic with ``<public_key>:<private_key>`` base64-encoded. The base
URL contains the merchant_id as a path segment, so it's constructed at
runtime from credentials.
"""
from __future__ import annotations

import base64
from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_BRAINTREE_VERSION = "2023-09-15"

_QUERIES: dict[str, str] = {
    "client_token_generate": """
        mutation { createClientToken(input: {}) { clientToken } }
    """,
    "transaction_sale": """
        mutation ChargePM($input: ChargePaymentMethodInput!) {
            chargePaymentMethod(input: $input) {
                transaction { id status }
            }
        }
    """,
    "transaction_refund": """
        mutation RefundTx($input: RefundTransactionInput!) {
            refundTransaction(input: $input) {
                refund { id status }
            }
        }
    """,
}


def _variables_for(op: str, payload: dict[str, Any]) -> dict[str, Any]:
    if op == "client_token_generate":
        return {}
    if op == "transaction_sale":
        return {
            "input": {
                "paymentMethodId": payload["paymentMethodId"],
                "transaction": {"amount": payload["amount"]},
            }
        }
    if op == "transaction_refund":
        body: dict[str, Any] = {"transactionId": payload["transactionId"]}
        if payload.get("amount"):
            body["refund"] = {"amount": payload["amount"]}
        return {"input": body}
    raise ValueError(f"unknown operation: {op!r}")


async def braintree_api(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch a Braintree GraphQL mutation.

    Selects op via ``_operation`` field on payload.
    """
    op = payload.get("_operation")
    if op not in _QUERIES:
        raise ValueError(f"unknown operation: {op!r}; expected one of {list(_QUERIES)}")

    creds = get_credentials(project_id=project_id, kind="tool", id="braintree_api")
    for required in ("BRAINTREE_PUBLIC_KEY", "BRAINTREE_PRIVATE_KEY", "BRAINTREE_MERCHANT_ID"):
        if not creds.get(required):
            raise RuntimeError(f"braintree_api: missing {required} credential")

    public_key = creds["BRAINTREE_PUBLIC_KEY"]
    private_key = creds["BRAINTREE_PRIVATE_KEY"]
    merchant_id = creds["BRAINTREE_MERCHANT_ID"]
    env = creds.get("BRAINTREE_ENV", "sandbox")

    if env == "production":
        base = f"https://api.braintreegateway.com/merchants/{merchant_id}"
    else:
        base = f"https://api.sandbox.braintreegateway.com/merchants/{merchant_id}"

    auth = base64.b64encode(f"{public_key}:{private_key}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Braintree-Version": _BRAINTREE_VERSION,
        "Content-Type": "application/json",
    }
    body = {"query": _QUERIES[op], "variables": _variables_for(op, payload)}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{base}/graphql", headers=headers, json=body)
    resp.raise_for_status()
    return resp.json()
