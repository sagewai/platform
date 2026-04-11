# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""In-memory notification store.

Stores notification history and channel configurations in memory.
Suitable for development and testing.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from sagewai.notifications.models import NotificationChannelConfig, NotificationRecord


class InMemoryNotificationStore:
    """Thread-safe in-memory store for notification history and config."""

    MAX_HISTORY = 1000

    def __init__(self) -> None:
        self._history: list[NotificationRecord] = []
        self._channel_configs: dict[str, NotificationChannelConfig] = {}
        self._trigger_routing: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def record(self, notification: NotificationRecord) -> None:
        """Append a notification record, evicting oldest if at capacity."""
        with self._lock:
            self._history.append(notification)
            if len(self._history) > self.MAX_HISTORY:
                self._history = self._history[-self.MAX_HISTORY :]

    def list_history(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        trigger: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return notification history with optional filtering."""
        with self._lock:
            items = list(reversed(self._history))  # newest first

        if trigger:
            items = [n for n in items if n.trigger == trigger]
        if project_id:
            items = [n for n in items if n.project_id == project_id]

        page = items[offset : offset + limit]
        return [n.model_dump(mode="json") for n in page]

    # ------------------------------------------------------------------
    # Channel configs
    # ------------------------------------------------------------------

    def save_channel_config(self, config: dict[str, Any] | NotificationChannelConfig) -> str:
        """Save a channel config, returning its key."""
        if isinstance(config, dict):
            project_id = config.get("project_id")
            channel_type = config.get("channel_type", "")
            model = NotificationChannelConfig(**config)
        else:
            project_id = config.project_id
            channel_type = config.channel_type
            model = config
        key = f"{project_id or '_system'}:{channel_type}"
        with self._lock:
            self._channel_configs[key] = model
        return key

    def list_channel_configs(
        self, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List channel configurations, optionally filtered by project."""
        with self._lock:
            configs = list(self._channel_configs.values())
        if project_id is not None:
            configs = [c for c in configs if c.project_id == project_id]
        result = []
        for c in configs:
            d = c.model_dump(mode="json")
            d["id"] = f"{c.project_id or '_system'}:{c.channel_type}"
            result.append(d)
        return result

    def delete_channel_config(self, config_id: str) -> bool:
        """Delete a channel config by its key. Returns True if found."""
        with self._lock:
            return self._channel_configs.pop(config_id, None) is not None

    # ------------------------------------------------------------------
    # Trigger routing
    # ------------------------------------------------------------------

    def save_trigger_routing(self, config: dict[str, Any]) -> str:
        """Save a trigger routing config, returning its key."""
        project_id = config.get("project_id")
        trigger = config.get("trigger", "")
        channel_type = config.get("channel_type", "")
        key = f"{project_id or '_system'}:{trigger}:{channel_type}"
        with self._lock:
            self._trigger_routing[key] = config
        return key

    def list_trigger_routing(
        self, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List trigger routing configs, optionally filtered by project."""
        with self._lock:
            configs = list(self._trigger_routing.values())
        if project_id is not None:
            configs = [c for c in configs if c.get("project_id") == project_id]
        result = []
        for c in configs:
            entry = dict(c)
            entry.setdefault(
                "id",
                f"{c.get('project_id') or '_system'}:{c.get('trigger')}:{c.get('channel_type')}",
            )
            result.append(entry)
        return result

    def delete_trigger_routing(self, trigger_id: str) -> bool:
        """Delete a trigger routing config by key. Returns True if found."""
        with self._lock:
            return self._trigger_routing.pop(trigger_id, None) is not None
