# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Atlassian Compass tool — service catalog via GraphQL.

Compass exposes a GraphQL endpoint at ``POST {site}/gateway/api/graphql``.
Auth is HTTP Basic with the operator's email + API token (the same
Atlassian token used for Jira/Confluence). Site URL is per-operator and
provided via the ``COMPASS_SITE`` credential.
"""
from __future__ import annotations

import base64
from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)

_QUERIES: dict[str, str] = {
    "get_component": """
        query GetComponent($id: ID!) {
            compass {
                component(id: $id) {
                    id
                    name
                    description
                    type
                }
            }
        }
    """,
    "list_components": """
        query ListComponents($cloudId: ID!) {
            compass {
                searchComponents(cloudId: $cloudId) {
                    nodes { id name type }
                }
            }
        }
    """,
    "create_event": """
        mutation CreateEvent($input: CompassCreateEventInput!) {
            compass {
                createEvent(input: $input) {
                    success
                }
            }
        }
    """,
}


def _variables_for(op: str, payload: dict[str, Any]) -> dict[str, Any]:
    if op == "get_component":
        return {"id": payload["id"]}
    if op == "list_components":
        return {"cloudId": payload["cloudId"]}
    if op == "create_event":
        return {"input": payload.get("input", {})}
    raise ValueError(f"unknown operation: {op!r}")


async def compass_api(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch a Compass GraphQL operation."""
    op = payload.get("_operation")
    if op not in _QUERIES:
        raise ValueError(f"unknown operation: {op!r}; expected one of {list(_QUERIES)}")

    creds = get_credentials(project_id=project_id, kind="tool", id="compass_api")
    email = creds.get("USERNAME")
    if not email:
        raise RuntimeError("compass_api: missing USERNAME credential (Atlassian email)")
    token = creds.get("PASSWORD")
    if not token:
        raise RuntimeError("compass_api: missing PASSWORD credential (Atlassian API token)")
    site = creds.get("COMPASS_SITE")
    if not site:
        raise RuntimeError("compass_api: missing COMPASS_SITE credential")

    site = site.rstrip("/")
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {"query": _QUERIES[op], "variables": _variables_for(op, payload)}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{site}/gateway/api/graphql", headers=headers, json=body)
    resp.raise_for_status()
    return resp.json()
