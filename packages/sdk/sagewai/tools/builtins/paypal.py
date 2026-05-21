# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PayPal tool — orders via OAuth2 client-credentials token exchange.

PayPal uses a server-to-server OAuth2 flow: exchange ``client_id:secret``
for a Bearer access token via ``POST /v1/oauth2/token``, cache it for the
returned TTL (typically 1 hour), refresh on expiry. From the operator's
perspective this is api_key (one credential pair, no user redirect).

Cache key is ``(project_id, client_id)`` so two projects sharing the
same credentials don't race for token. Cache is in-memory only — first
request after process restart triggers a fresh exchange.
"""
from __future__ import annotations

import string
import time
from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_TOKEN_TTL_SECONDS = 3600
_TOKEN_SAFETY_BUFFER = 60

# Cache key: (project_id, client_id). Value: (access_token, issued_at_monotonic).
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}

_OPS: dict[str, dict[str, str]] = {
    "create_order":   {"method": "POST", "path": "/v2/checkout/orders"},
    "capture_order":  {"method": "POST", "path": "/v2/checkout/orders/{order_id}/capture"},
    "get_order":      {"method": "GET",  "path": "/v2/checkout/orders/{order_id}"},
    "refund_capture": {"method": "POST", "path": "/v2/payments/captures/{capture_id}/refund"},
}


def _api_base(env: str) -> str:
    return "https://api-m.paypal.com" if env == "live" else "https://api-m.sandbox.paypal.com"


async def _get_access_token(
    client: httpx.AsyncClient,
    env: str,
    client_id: str,
    secret: str,
    project_id: str,
) -> str:
    cache_key = (project_id, client_id)
    cached = _TOKEN_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and (now - cached[1]) < (_TOKEN_TTL_SECONDS - _TOKEN_SAFETY_BUFFER):
        return cached[0]

    resp = await client.post(
        f"{_api_base(env)}/v1/oauth2/token",
        auth=(client_id, secret),
        data={"grant_type": "client_credentials"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _TOKEN_CACHE[cache_key] = (token, now)
    return token


async def paypal_api(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch a PayPal API call with cached access token.

    Selects op via ``_operation`` field on payload.
    """
    op = payload.get("_operation")
    if op not in _OPS:
        raise ValueError(f"unknown operation: {op!r}; expected one of {list(_OPS)}")

    creds = get_credentials(project_id=project_id, kind="tool", id="paypal_api")
    client_id = creds.get("PAYPAL_CLIENT_ID")
    if not client_id:
        raise RuntimeError("paypal_api: missing PAYPAL_CLIENT_ID credential")
    secret = creds.get("PAYPAL_SECRET")
    if not secret:
        raise RuntimeError("paypal_api: missing PAYPAL_SECRET credential")
    env = creds.get("PAYPAL_ENV", "sandbox")

    op_spec = _OPS[op]
    path = op_spec["path"]
    body = {k: v for k, v in payload.items() if k != "_operation"}

    # Substitute path placeholders from payload (e.g. {order_id}, {capture_id})
    placeholders = {name for _, name, _, _ in string.Formatter().parse(path) if name}
    if placeholders:
        path_args = {name: body.pop(name) for name in placeholders if name in body}
        path = path.format(**path_args)

    method = op_spec["method"]

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        token = await _get_access_token(client, env, client_id, secret, project_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        url = f"{_api_base(env)}{path}"
        if method == "GET":
            resp = await client.get(url, headers=headers)
        else:
            resp = await client.request(method, url, headers=headers, json=body if body else None)
    resp.raise_for_status()
    return resp.json()
