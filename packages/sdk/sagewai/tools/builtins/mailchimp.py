# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Mailchimp API tool — add subscribers and trigger campaigns.

Mailchimp API keys carry a ``-<datacenter>`` suffix (e.g. ``abc123-us21``)
that determines the base URL: ``https://<dc>.api.mailchimp.com/3.0``.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)


def _parse_datacenter(api_key: str) -> str:
    if "-" not in api_key:
        raise ValueError(
            f"mailchimp api key {api_key[:6]!r}... is missing the datacenter suffix "
            "(expected format: <32-hex>-<dc>, e.g. abc123-us21)"
        )
    return api_key.rsplit("-", 1)[-1]


async def _add_subscriber(client, base_url, api_key, payload):
    body: dict[str, Any] = {
        "email_address": payload["email"],
        "status": payload.get("status", "subscribed"),
    }
    if payload.get("merge_fields"):
        body["merge_fields"] = payload["merge_fields"]
    if payload.get("tags"):
        body["tags"] = payload["tags"]
    resp = await client.post(
        f"{base_url}/lists/{payload['list_id']}/members",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"id": data["id"], "email": data["email_address"], "status": data["status"]}


async def _send_campaign(client, base_url, api_key, payload):
    resp = await client.post(
        f"{base_url}/campaigns/{payload['campaign_id']}/actions/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    resp.raise_for_status()
    return {"sent": True, "campaign_id": payload["campaign_id"]}


_OPS = {
    "add_subscriber": _add_subscriber,
    "send_campaign": _send_campaign,
}


async def mailchimp_api(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Mailchimp tool — two operations selected via ``_operation`` key.

    Operations:
    - ``add_subscriber``: requires ``list_id`` + ``email``; optional ``status``,
      ``merge_fields``, ``tags``.
    - ``send_campaign``: requires ``campaign_id``.
    """
    op = payload.get("_operation")
    if op not in _OPS:
        raise ValueError(f"unknown operation: {op!r}; expected one of {list(_OPS)}")

    creds = get_credentials(project_id=project_id, kind="tool", id="mailchimp_api")
    api_key = creds.get("MAILCHIMP_API_KEY", "")
    if not api_key:
        raise RuntimeError("mailchimp_api: missing MAILCHIMP_API_KEY credential")

    dc = _parse_datacenter(api_key)
    base_url = f"https://{dc}.api.mailchimp.com/3.0"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        return await _OPS[op](client, base_url, api_key, payload)
