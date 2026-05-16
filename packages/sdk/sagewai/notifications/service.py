# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Notification service — orchestrates sending notifications via channels.

Supports system-wide and per-project channel configuration, trigger-based
routing, and notification history recording.

Usage::

    from sagewai.notifications.service import NotificationService
    from sagewai.notifications.channels.slack import SlackWebhookChannel

    svc = NotificationService()
    svc.register_channel("slack", SlackWebhookChannel(webhook_url="..."))
    await svc.notify("budget_warning", "Budget alert", "Limit approaching")
"""

from __future__ import annotations

import inspect
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sagewai.notifications.channels.base import NotificationChannel
from sagewai.notifications.models import NotificationRecord

logger = logging.getLogger(__name__)


class NotificationService:
    """Central notification dispatcher.

    Routes notifications to the appropriate channels based on trigger type
    and project context. Records delivery results in a history store.
    """

    def __init__(self, store: Any = None) -> None:
        self._store = store
        self._system_channels: dict[str, NotificationChannel] = {}
        self._project_channels: dict[str, dict[str, NotificationChannel]] = {}
        self._trigger_routing: dict[str, list[str]] = {
            "budget_warning": ["email", "slack", "in_app"],
            "budget_exceeded": ["email", "slack", "in_app"],
            "budget_throttled": ["email", "slack", "in_app"],
            "workflow_failed": ["email", "slack", "in_app"],
            "approval_requested": ["in_app"],
        }

    # ------------------------------------------------------------------
    # Channel registration
    # ------------------------------------------------------------------

    def register_channel(
        self,
        channel_type: str,
        channel: NotificationChannel,
        project_id: str | None = None,
    ) -> None:
        """Register a notification channel.

        Args:
            channel_type: Channel identifier (e.g. "email", "slack", "in_app").
            channel: The channel implementation.
            project_id: If set, registers as a project-specific override.
        """
        if project_id is None:
            self._system_channels[channel_type] = channel
        else:
            if project_id not in self._project_channels:
                self._project_channels[project_id] = {}
            self._project_channels[project_id][channel_type] = channel

    def set_trigger_routing(
        self, trigger: str, channel_types: list[str]
    ) -> None:
        """Configure which channel types a trigger routes to."""
        self._trigger_routing[trigger] = list(channel_types)

    def get_trigger_routing(self) -> dict[str, list[str]]:
        """Return the current trigger routing configuration."""
        return dict(self._trigger_routing)

    # ------------------------------------------------------------------
    # Channel resolution
    # ------------------------------------------------------------------

    def _resolve_channel(
        self, channel_type: str, project_id: str | None
    ) -> NotificationChannel | None:
        """Resolve a channel: project override > system default."""
        if project_id and project_id in self._project_channels:
            ch = self._project_channels[project_id].get(channel_type)
            if ch is not None:
                return ch
        return self._system_channels.get(channel_type)

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    async def notify(
        self,
        trigger: str,
        title: str,
        body: str,
        severity: str = "info",
        project_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[bool]:
        """Send a notification through all channels configured for this trigger.

        Returns a list of booleans indicating delivery success per channel.
        """
        channel_types = self._trigger_routing.get(trigger, [])
        if not channel_types:
            logger.debug("No channels configured for trigger '%s'", trigger)
            return []

        results: list[bool] = []
        metadata: dict[str, Any] = {
            "trigger": trigger,
            "project_id": project_id,
            "agent_name": agent_name,
        }

        for ct in channel_types:
            channel = self._resolve_channel(ct, project_id)
            if channel is None:
                continue

            try:
                ok = await channel.send(title, body, severity, metadata)
            except Exception as exc:
                logger.error(
                    "Channel %s raised during send: %s", ct, exc
                )
                ok = False

            results.append(ok)

            # Record in history store
            if self._store is not None:
                record = NotificationRecord(
                    id=uuid.uuid4().hex,
                    trigger=trigger,
                    title=title,
                    body=body,
                    severity=severity,  # type: ignore[arg-type]
                    channel_type=ct,
                    project_id=project_id,
                    agent_name=agent_name,
                    delivered=ok,
                    error=None if ok else "delivery failed",
                    created_at=datetime.now(timezone.utc),
                )
                try:
                    if hasattr(self._store, "record"):
                        result = self._store.record(record)
                        if inspect.isawaitable(result):
                            await result
                except Exception as exc:
                    logger.warning("Failed to record notification: %s", exc)

        return results
