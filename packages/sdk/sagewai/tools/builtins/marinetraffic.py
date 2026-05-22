# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""MarineTraffic tool — vessel tracking via the MarineTraffic API.

MarineTraffic embeds the API key as a URL path segment
(``/api/{service}/v:{ver}/{key}/...``) and takes request parameters as
colon-delimited path segments, not a query string — so this tool ships
as ``kind: sdk``.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_BASE = "https://services.marinetraffic.com/api"

_SERVICES: dict[str, tuple[str, str]] = {
    "vessel_positions": ("exportvessel", "8"),
    "port_calls": ("portcalls", "5"),
    "vessel_details": ("vesselmasterdata", "1"),
}


async def marinetraffic(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch a MarineTraffic API operation."""
    op = payload.get("_operation")
    if op not in _SERVICES:
        raise ValueError(
            f"unknown operation: {op!r}; expected one of {list(_SERVICES)}"
        )
    service, version = _SERVICES[op]

    creds = get_credentials(
        project_id=project_id, kind="tool", id="marinetraffic_api"
    )
    key = creds.get("MARINETRAFFIC_API_KEY")
    if not key:
        raise RuntimeError(
            "marinetraffic: missing MARINETRAFFIC_API_KEY credential"
        )

    params = payload.get("params", {})
    segments = "".join(f"/{name}:{value}" for name, value in params.items())
    url = f"{_BASE}/{service}/v:{version}/{key}{segments}/protocol:jsono"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return {"data": resp.json()}
