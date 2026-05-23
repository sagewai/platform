# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Terra tool — wearables aggregator via the Terra v2 API.

Terra requires both ``x-api-key`` and ``dev-id`` headers on every
request, which the generic http executor cannot supply — this tool
ships as ``kind: sdk``.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_BASE = "https://api.tryterra.co/v2"
_OPS = ("list_users", "get_daily", "get_sleep", "get_body", "get_activity")


async def terra(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch a Terra v2 API operation."""
    op = payload.get("_operation")
    if op not in _OPS:
        raise ValueError(
            f"unknown operation: {op!r}; expected one of {list(_OPS)}"
        )

    creds = get_credentials(project_id=project_id, kind="tool", id="terra_api")
    api_key = creds.get("TERRA_API_KEY")
    dev_id = creds.get("TERRA_DEV_ID")
    if not api_key:
        raise RuntimeError("terra: missing TERRA_API_KEY credential")
    if not dev_id:
        raise RuntimeError("terra: missing TERRA_DEV_ID credential")

    headers = {
        "x-api-key": api_key,
        "dev-id": dev_id,
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if op == "list_users":
            resp = await client.get(f"{_BASE}/userInfo", headers=headers)
        else:
            path = {
                "get_daily": "/daily",
                "get_sleep": "/sleep",
                "get_body": "/body",
                "get_activity": "/activity",
            }[op]
            params = {
                "user_id": payload["user_id"],
                "start_date": payload["start_date"],
                "end_date": payload["end_date"],
            }
            resp = await client.get(
                f"{_BASE}{path}", headers=headers, params=params
            )
    resp.raise_for_status()
    return resp.json()
