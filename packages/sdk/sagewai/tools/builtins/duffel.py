# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Duffel tool — flight search via the Duffel Flights API.

Duffel exposes a REST API at ``https://api.duffel.com``. Every request
needs an ``Authorization: Bearer`` header plus a ``Duffel-Version``
header — the latter cannot be added by the generic http executor, which
is why this tool ships as ``kind: sdk``.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_BASE = "https://api.duffel.com"
_DUFFEL_VERSION = "v2"

_OPS = ("search_flights", "list_offers", "get_offer")


async def duffel(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch a Duffel Flights API operation."""
    op = payload.get("_operation")
    if op not in _OPS:
        raise ValueError(
            f"unknown operation: {op!r}; expected one of {list(_OPS)}"
        )

    creds = get_credentials(project_id=project_id, kind="tool", id="duffel_api")
    token = creds.get("DUFFEL_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("duffel: missing DUFFEL_ACCESS_TOKEN credential")

    headers = {
        "Authorization": f"Bearer {token}",
        "Duffel-Version": _DUFFEL_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if op == "search_flights":
            data: dict[str, Any] = {
                "slices": payload["slices"],
                "passengers": payload["passengers"],
            }
            if "cabin_class" in payload:
                data["cabin_class"] = payload["cabin_class"]
            resp = await client.post(
                f"{_BASE}/air/offer_requests",
                headers=headers,
                params={"return_offers": "true"},
                json={"data": data},
            )
        elif op == "list_offers":
            resp = await client.get(
                f"{_BASE}/air/offers",
                headers=headers,
                params={"offer_request_id": payload["offer_request_id"]},
            )
        else:  # get_offer
            resp = await client.get(
                f"{_BASE}/air/offers/{payload['id']}", headers=headers
            )
    resp.raise_for_status()
    return resp.json()
