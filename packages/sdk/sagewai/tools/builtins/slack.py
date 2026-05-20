# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Slack bot tool — post messages to channels and threads.

Slack returns HTTP 200 with ``{ok: false, error: ...}`` on semantic
failure (invalid channel, missing scope). We check the ``ok`` field and
raise :class:`SlackAPIError` rather than trust the HTTP status.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_ENDPOINT = "https://slack.com/api/chat.postMessage"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)


class SlackAPIError(RuntimeError):
    """Slack returned ok=false; carries the Slack error code."""


async def post_to_slack(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Post a message to a Slack channel or thread.

    Payload required: ``channel``, ``text``.
    Payload optional: ``thread_ts``, ``blocks``, ``attachments``.
    """
    creds = get_credentials(project_id=project_id, kind="tool", id="post_to_slack")
    token = creds.get("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("post_to_slack: missing SLACK_BOT_TOKEN credential")

    body: dict[str, Any] = {
        "channel": payload["channel"],
        "text": payload["text"],
    }
    for opt in ("thread_ts", "blocks", "attachments"):
        if opt in payload:
            body[opt] = payload[opt]

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            _ENDPOINT,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=body,
        )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise SlackAPIError(data.get("error", "unknown_error"))
    return {"ok": True, "ts": data["ts"], "channel": data["channel"]}
