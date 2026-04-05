# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Notification system — email, Slack, and in-app alert channels.

Provides a NotificationService that dispatches alerts to registered
channels based on event triggers (budget warnings, workflow failures, etc.).
"""

from sagewai.notifications.channels.base import NotificationChannel
from sagewai.notifications.channels.email import SMTPChannel
from sagewai.notifications.channels.inapp import InAppChannel
from sagewai.notifications.channels.slack import SlackWebhookChannel
from sagewai.notifications.hooks import (
    create_budget_notification_hook,
    create_workflow_notification_hook,
)
from sagewai.notifications.service import NotificationService
from sagewai.notifications.stores import InMemoryNotificationStore

__all__ = [
    "InAppChannel",
    "InMemoryNotificationStore",
    "NotificationChannel",
    "NotificationService",
    "SMTPChannel",
    "SlackWebhookChannel",
    "create_budget_notification_hook",
    "create_workflow_notification_hook",
]
