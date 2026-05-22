# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Amplitude product analytics tool.

Amplitude's HTTP API V2 expects the api_key in the request body
(``{"api_key": ..., "events": [...]}``), not in a header. We ship as
``kind: sdk`` so the builtin injects the key from credentials, keeping
it out of blueprint payloads.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_BASE = "https://api2.amplitude.com"


async def amplitude_api(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch Amplitude API calls (V2 HTTP API + Identify).

    Selects op via ``_operation`` field. Injects the api_key from
    credentials into the request body — operators never need to put it
    in blueprint payloads.
    """
    op = payload.get("_operation")
    if op not in ("track_event", "identify_user"):
        raise ValueError(f"unknown operation: {op!r}; expected one of ['track_event', 'identify_user']")

    creds = get_credentials(project_id=project_id, kind="tool", id="amplitude_api")
    api_key = creds.get("AMPLITUDE_API_KEY")
    if not api_key:
        raise RuntimeError("amplitude_api: missing AMPLITUDE_API_KEY credential")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if op == "track_event":
            body = {"api_key": api_key, "events": payload.get("events", [])}
            resp = await client.post(
                f"{_BASE}/2/httpapi",
                json=body,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()
        else:
            data = {
                "api_key": api_key,
                "identification": json.dumps(payload.get("identification", [])),
            }
            resp = await client.post(
                f"{_BASE}/identify",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            return {"status": resp.status_code, "body": resp.text}
