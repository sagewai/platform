# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OpenFDA tool — FDA adverse events, drug labels, device events, recalls.

OpenFDA accepts an optional ``?api_key=`` query parameter for higher
rate limits. When the credential is absent the request is sent
unauthenticated (lower rate limit). Ships as ``kind: sdk`` because the
http executor cannot place auth in the query string and cannot
conditionally omit it.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_BASE = "https://api.fda.gov"
_OPS = {
    "drug_event": "/drug/event.json",
    "drug_label": "/drug/label.json",
    "device_event": "/device/event.json",
    "food_enforcement": "/food/enforcement.json",
}


async def openfda(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch an OpenFDA API operation. API key is optional."""
    op = payload.get("_operation")
    if op not in _OPS:
        raise ValueError(
            f"unknown operation: {op!r}; expected one of {list(_OPS)}"
        )

    creds = get_credentials(project_id=project_id, kind="tool", id="openfda_api")
    key = creds.get("OPENFDA_API_KEY")

    params: dict[str, Any] = {"search": payload["search"]}
    for k in ("limit", "skip"):
        if k in payload:
            params[k] = payload[k]
    if key:
        params["api_key"] = key

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{_BASE}{_OPS[op]}", params=params)
    resp.raise_for_status()
    return resp.json()
