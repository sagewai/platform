# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""USDA FoodData Central tool.

The USDA FDC API takes the API key as a query-string parameter
(``?api_key=...``) rather than a header — the generic http executor
only supports header-based auth, so this tool ships as ``kind: sdk``.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_BASE = "https://api.nal.usda.gov/fdc/v1"
_OPS = ("search_foods", "get_food", "list_foods")


async def usda_fdc(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch a USDA FoodData Central API operation."""
    op = payload.get("_operation")
    if op not in _OPS:
        raise ValueError(
            f"unknown operation: {op!r}; expected one of {list(_OPS)}"
        )

    creds = get_credentials(project_id=project_id, kind="tool", id="usda_fdc_api")
    key = creds.get("USDA_FDC_API_KEY")
    if not key:
        raise RuntimeError("usda_fdc: missing USDA_FDC_API_KEY credential")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if op == "search_foods":
            params: dict[str, Any] = {"api_key": key, "query": payload["query"]}
            if "dataType" in payload:
                params["dataType"] = payload["dataType"]
            if "pageSize" in payload:
                params["pageSize"] = payload["pageSize"]
            resp = await client.get(f"{_BASE}/foods/search", params=params)
        elif op == "get_food":
            resp = await client.get(
                f"{_BASE}/food/{payload['fdcId']}",
                params={"api_key": key},
            )
        else:  # list_foods
            params = {"api_key": key}
            for k in ("dataType", "pageSize", "pageNumber"):
                if k in payload:
                    params[k] = payload[k]
            resp = await client.get(f"{_BASE}/foods/list", params=params)
    resp.raise_for_status()
    return resp.json()
