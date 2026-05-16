# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Abstract base class for notification channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class NotificationChannel(ABC):
    """Base class for all notification channels.

    Subclasses implement ``send()`` to deliver notifications via their
    respective transport (email, Slack webhook, in-app event bus, etc.).
    """

    @abstractmethod
    async def send(
        self,
        title: str,
        body: str,
        severity: str,
        metadata: dict[str, Any],
    ) -> bool:
        """Send a notification.

        Returns ``True`` on success, ``False`` on failure.
        Implementations must not raise — they should catch and log errors.
        """
        ...
