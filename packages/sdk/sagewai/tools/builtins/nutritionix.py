# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Nutritionix tool — restaurant & branded food database.

Nutritionix requires both ``x-app-id`` and ``x-app-key`` headers on
every request — ships as ``kind: sdk``.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_BASE = "https://trackapi.nutritionix.com/v2"
_OPS = ("search_instant", "natural_nutrients", "search_item")


async def nutritionix(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch a Nutritionix v2 API operation."""
    op = payload.get("_operation")
    if op not in _OPS:
        raise ValueError(
            f"unknown operation: {op!r}; expected one of {list(_OPS)}"
        )

    creds = get_credentials(project_id=project_id, kind="tool", id="nutritionix_api")
    app_id = creds.get("NUTRITIONIX_APP_ID")
    app_key = creds.get("NUTRITIONIX_APP_KEY")
    if not app_id:
        raise RuntimeError("nutritionix: missing NUTRITIONIX_APP_ID credential")
    if not app_key:
        raise RuntimeError("nutritionix: missing NUTRITIONIX_APP_KEY credential")

    headers = {
        "x-app-id": app_id,
        "x-app-key": app_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if op == "search_instant":
            resp = await client.get(
                f"{_BASE}/search/instant",
                headers=headers,
                params={"query": payload["query"]},
            )
        elif op == "natural_nutrients":
            resp = await client.post(
                f"{_BASE}/natural/nutrients",
                headers=headers,
                json={"query": payload["query"]},
            )
        else:  # search_item
            resp = await client.get(
                f"{_BASE}/search/item",
                headers=headers,
                params={"nix_item_id": payload["nix_item_id"]},
            )
    resp.raise_for_status()
    return resp.json()
