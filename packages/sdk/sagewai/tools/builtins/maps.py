# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Google Maps Directions API.

Why ``kind: sdk``: the Directions API uses query-string ``?key=...``
authentication, not a header. The http executor's static ``auth`` shape
doesn't accommodate query-param keys, so a small builtin handles
URL construction here.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_ENDPOINT = "https://maps.googleapis.com/maps/api/directions/json"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)


async def maps_route(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Compute directions between origin and destination.

    Payload required: ``origin``, ``destination``.
    Payload optional: ``mode`` (default "driving"), ``waypoints`` (list[str]
    joined with ``|``).

    Returns ``{status, routes: [...]}`` from the Google Maps API response.
    """
    creds = get_credentials(project_id=project_id, kind="tool", id="maps_route")
    api_key = creds.get("MAPS_API_KEY")
    if not api_key:
        raise RuntimeError("maps_route: missing MAPS_API_KEY credential")

    params: dict[str, str] = {
        "origin": payload["origin"],
        "destination": payload["destination"],
        "mode": payload.get("mode", "driving"),
        "key": api_key,
    }
    if payload.get("waypoints"):
        params["waypoints"] = "|".join(payload["waypoints"])

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(_ENDPOINT, params=params)
    resp.raise_for_status()
    return resp.json()
