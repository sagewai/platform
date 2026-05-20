# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Discord bot tool — post messages to channels.

Discord uses ``Authorization: Bot <token>`` (NOT ``Bearer``). Content is
capped at 2000 chars; we validate pre-HTTP. Rate limits are per-channel
via 429 + ``retry_after``; one retry-with-backoff, then graceful
``{rate_limited: true}`` degradation.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

import httpx

_BASE_URL = "https://discord.com/api/v10"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_MAX_CONTENT = 2000


async def discord_api(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Post a message to a Discord channel."""
    content = payload["content"]
    if len(content) > _MAX_CONTENT:
        raise ValueError(f"discord content exceeds 2000 char limit (got {len(content)})")

    creds = get_credentials(project_id=project_id, kind="tool", id="discord_api")
    token = creds.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("discord_api: missing DISCORD_BOT_TOKEN credential")

    channel_id = payload["channel_id"]
    body: dict[str, Any] = {"content": content}
    for opt in ("embeds", "components"):
        if opt in payload:
            body[opt] = payload[opt]

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    url = f"{_BASE_URL}/channels/{channel_id}/messages"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "1"))
            await asyncio.sleep(min(retry_after, 5.0))
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code == 429:
                return {"rate_limited": True}

    resp.raise_for_status()
    data = resp.json()
    return {"id": data["id"], "channel_id": data["channel_id"], "content": data.get("content", "")}
