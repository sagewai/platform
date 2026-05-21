# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Plaid tool — banking + financial accounts.

Plaid sends both ``PLAID-CLIENT-ID`` and ``PLAID-SECRET`` as request
headers. The http executor's single-credential model doesn't fit, so
this builtin constructs both headers directly.

The base URL switches on ``PLAID_ENV`` (sandbox, development, production).
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_VALID_ENVS = {"sandbox", "development", "production"}

_OPS: dict[str, str] = {
    "link_token_create": "/link/token/create",
    "item_public_token_exchange": "/item/public_token/exchange",
    "accounts_get": "/accounts/get",
    "transactions_get": "/transactions/get",
}


async def plaid_api(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch Plaid API calls via two-header auth + env-switched base URL.

    Selects op via ``_operation`` field on payload (set by the factory).
    """
    op = payload.get("_operation")
    if op not in _OPS:
        raise ValueError(f"unknown operation: {op!r}; expected one of {list(_OPS)}")

    creds = get_credentials(project_id=project_id, kind="tool", id="plaid_api")
    client_id = creds.get("PLAID_CLIENT_ID")
    if not client_id:
        raise RuntimeError("plaid_api: missing PLAID_CLIENT_ID credential")
    secret = creds.get("PLAID_SECRET")
    if not secret:
        raise RuntimeError("plaid_api: missing PLAID_SECRET credential")
    env = creds.get("PLAID_ENV", "sandbox")
    if env not in _VALID_ENVS:
        raise RuntimeError(
            f"plaid_api: invalid PLAID_ENV {env!r}; expected one of {sorted(_VALID_ENVS)}"
        )

    base_url = f"https://{env}.plaid.com"
    body = {k: v for k, v in payload.items() if k != "_operation"}
    headers = {
        "PLAID-CLIENT-ID": client_id,
        "PLAID-SECRET": secret,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{base_url}{_OPS[op]}", headers=headers, json=body)
    resp.raise_for_status()
    return resp.json()
