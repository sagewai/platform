# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Datadog observability tool — log search, metric submission, monitor listing.

Uses dual-header auth: ``DD-API-KEY`` (data ingestion) + ``DD-APPLICATION-KEY``
(query / management APIs). Base URL switches on ``DATADOG_SITE``
(datadoghq.com / datadoghq.eu / us3.datadoghq.com / us5.datadoghq.com /
ap1.datadoghq.com), defaulting to ``datadoghq.com``.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)

_OPS: dict[str, dict[str, str]] = {
    "search_logs":   {"method": "POST", "path": "/api/v2/logs/events/search"},
    "submit_metric": {"method": "POST", "path": "/api/v2/series"},
    "list_monitors": {"method": "GET",  "path": "/api/v1/monitor"},
}


async def datadog_api(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch Datadog API calls with dual-header auth + region-switched base URL."""
    op = payload.get("_operation")
    if op not in _OPS:
        raise ValueError(f"unknown operation: {op!r}; expected one of {list(_OPS)}")

    creds = get_credentials(project_id=project_id, kind="tool", id="datadog_api")
    api_key = creds.get("DATADOG_API_KEY")
    if not api_key:
        raise RuntimeError("datadog_api: missing DATADOG_API_KEY credential")
    app_key = creds.get("DATADOG_APPLICATION_KEY")
    if not app_key:
        raise RuntimeError("datadog_api: missing DATADOG_APPLICATION_KEY credential")
    site = creds.get("DATADOG_SITE", "datadoghq.com")

    base_url = f"https://api.{site}"
    spec = _OPS[op]
    headers = {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
        "Content-Type": "application/json",
    }

    body = {k: v for k, v in payload.items() if k != "_operation"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        url = f"{base_url}{spec['path']}"
        if spec["method"] == "GET":
            resp = await client.get(url, headers=headers, params=body or None)
        else:
            resp = await client.post(url, headers=headers, json=body)
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}
