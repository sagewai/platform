# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""In-app notification channel.

Delivers notifications via a callback function or event bus, enabling
real-time SSE-based alerts in the admin UI.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sagewai.notifications.channels.base import NotificationChannel

logger = logging.getLogger(__name__)


class InAppChannel(NotificationChannel):
    """Publish notifications to an in-app event bus or callback.

    Always returns ``True`` (best-effort delivery).
    """

    def __init__(
        self,
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._callback = callback

    async def send(
        self,
        title: str,
        body: str,
        severity: str,
        metadata: dict[str, Any],
    ) -> bool:
        """Publish notification as an in-app event."""
        event = {
            "type": "notification",
            "title": title,
            "body": body,
            "severity": severity,
            **metadata,
        }
        try:
            if self._callback is not None:
                self._callback(event)
            logger.debug("In-app notification published: %s", title)
            return True
        except Exception as exc:
            logger.error("InAppChannel callback failed: %s", exc)
            return True  # best-effort — don't fail the notification flow
