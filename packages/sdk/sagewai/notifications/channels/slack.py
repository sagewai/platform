# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Slack webhook notification channel.

Posts Slack Block Kit messages to an incoming webhook URL using httpx.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from sagewai.notifications.channels.base import NotificationChannel

logger = logging.getLogger(__name__)


class SlackWebhookChannel(NotificationChannel):
    """Send notifications to Slack via an incoming webhook."""

    def __init__(
        self,
        *,
        webhook_url: str,
        channel: str | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.channel = channel

    async def send(
        self,
        title: str,
        body: str,
        severity: str,
        metadata: dict[str, Any],
    ) -> bool:
        """Post a Block Kit message to the Slack webhook."""
        severity_emoji = {
            "info": ":information_source:",
            "warning": ":warning:",
            "critical": ":rotating_light:",
        }
        emoji = severity_emoji.get(severity, ":bell:")

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {title}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": body,
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Severity:* {severity.upper()}",
                    },
                ],
            },
        ]

        # Add agent name to context if present
        if metadata.get("agent_name"):
            blocks[-1]["elements"].append({
                "type": "mrkdwn",
                "text": f"*Agent:* {metadata['agent_name']}",
            })

        payload: dict[str, Any] = {"blocks": blocks}
        if self.channel:
            payload["channel"] = self.channel

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.webhook_url, json=payload)
                resp.raise_for_status()
            logger.info("Slack notification sent: %s", title)
            return True
        except Exception as exc:
            logger.error("SlackWebhookChannel send failed: %s", exc)
            return False
