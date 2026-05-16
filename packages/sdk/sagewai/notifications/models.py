# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Notification data models.

Pydantic v2 models for notification channel configuration and history records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class NotificationChannelConfig(BaseModel):
    """Configuration for a notification channel."""

    channel_type: Literal["email", "slack", "in_app"]
    enabled: bool = True

    # Email (SMTP) settings
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    from_address: str | None = None
    to_addresses: list[str] = Field(default_factory=list)

    # Slack settings
    webhook_url: str | None = None
    slack_channel: str | None = None

    # Scope
    project_id: str | None = None  # None = system-wide default


class NotificationRecord(BaseModel):
    """A record of a sent (or attempted) notification."""

    id: str
    trigger: str
    title: str
    body: str
    severity: Literal["info", "warning", "critical"] = "info"
    channel_type: str
    project_id: str | None = None
    agent_name: str | None = None
    delivered: bool = False
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
