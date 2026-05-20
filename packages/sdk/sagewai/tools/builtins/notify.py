# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""In-platform notification dispatch.

Channels:
- log        — emit a structured log line
- event_bus  — publish on the mission's event bus

Outbound SaaS channels (Slack, email) land in the api_key tier of
sub-project 2.
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger("sagewai.tools.notify")


async def notify(payload: dict[str, Any]) -> dict[str, Any]:
    channel = payload.get("channel", "log")
    subject = payload["subject"]
    body = payload["body"]
    mission_id = payload.get("mission_id")

    if channel == "log":
        _log.info(
            "notify",
            extra={"subject": subject, "body": body, "mission_id": mission_id},
        )
        return {"delivered": True, "channel": "log"}

    if channel == "event_bus":
        if mission_id is None:
            raise ValueError("channel=event_bus requires mission_id")
        from sagewai.tools.builtins.mission_state import _resolver
        m = _resolver(mission_id)
        m.publish_event("notification.dispatched", {"subject": subject, "body": body})
        return {"delivered": True, "channel": "event_bus"}

    raise ValueError(f"unknown channel: {channel!r}")
