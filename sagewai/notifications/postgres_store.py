# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""PostgreSQL-backed notification store.

Persists notification history, channel configs, and trigger routing
to PostgreSQL using asyncpg.

Usage::

    from sagewai.notifications.postgres_store import PostgresNotificationStore

    store = PostgresNotificationStore(pool=pool)
    await store.record(notification)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sagewai.notifications.models import NotificationRecord

logger = logging.getLogger(__name__)


class PostgresNotificationStore:
    """PostgreSQL-backed store for notification history and configuration.

    Uses an asyncpg pool for safe concurrent access. Tables must exist
    (created by Alembic migration 024).
    """

    def __init__(
        self,
        pool: Any,
        encryption_key: str | None = None,
    ) -> None:
        self._pool = pool
        self._fernet: Any = None
        if encryption_key:
            try:
                from cryptography.fernet import Fernet

                self._fernet = Fernet(encryption_key.encode())
            except ImportError:
                logger.warning(
                    "cryptography not installed — sensitive fields "
                    "will be stored in plaintext"
                )

    def _encrypt(self, value: str) -> str:
        """Encrypt a string value if Fernet is available."""
        if self._fernet and value:
            return self._fernet.encrypt(value.encode()).decode()
        return value

    def _decrypt(self, value: str) -> str:
        """Decrypt a string value if Fernet is available."""
        if self._fernet and value:
            try:
                return self._fernet.decrypt(value.encode()).decode()
            except Exception:
                return value  # not encrypted or wrong key
        return value

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def record(self, notification: NotificationRecord) -> None:
        """Insert a notification record into history."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO notification_history "
                "(project_id, trigger, title, body, severity, agent_name, "
                "channel_type, delivered, error, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                notification.project_id,
                notification.trigger,
                notification.title,
                notification.body,
                notification.severity,
                notification.agent_name,
                notification.channel_type,
                notification.delivered,
                notification.error,
                notification.created_at,
            )

    async def list_history(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        trigger: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query notification history with optional filtering."""
        clauses: list[str] = []
        params: list[Any] = []
        idx = 1

        if trigger:
            clauses.append(f"trigger = ${idx}")
            params.append(trigger)
            idx += 1
        if project_id:
            clauses.append(f"project_id = ${idx}")
            params.append(project_id)
            idx += 1

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])

        query = (
            f"SELECT id, project_id, trigger, title, body, severity, "
            f"agent_name, channel_type, delivered, error, created_at "
            f"FROM notification_history{where} "
            f"ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        )

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [
            {
                "id": str(row["id"]),
                "project_id": row["project_id"],
                "trigger": row["trigger"],
                "title": row["title"],
                "body": row["body"],
                "severity": row["severity"],
                "agent_name": row["agent_name"],
                "channel_type": row["channel_type"],
                "delivered": row["delivered"],
                "error": row["error"],
                "created_at": row["created_at"].isoformat()
                if row["created_at"]
                else None,
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Channel configs
    # ------------------------------------------------------------------

    async def save_channel_config(
        self, config: dict[str, Any]
    ) -> int:
        """Upsert a channel config. Returns the row id."""
        project_id = config.get("project_id")
        channel_type = config.get("channel_type", "")

        # Encrypt sensitive fields
        cfg = dict(config)
        for key in ("smtp_password", "webhook_url"):
            if key in cfg and cfg[key]:
                cfg[key] = self._encrypt(cfg[key])

        # Store non-core fields in config JSONB
        core_keys = {"project_id", "channel_type", "enabled"}
        extra = {k: v for k, v in cfg.items() if k not in core_keys and k != "id"}
        enabled = cfg.get("enabled", True)

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO notification_channels "
                "(project_id, channel_type, enabled, config) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (project_id, channel_type) "
                "DO UPDATE SET enabled = EXCLUDED.enabled, "
                "config = EXCLUDED.config, "
                "updated_at = NOW() "
                "RETURNING id",
                project_id,
                channel_type,
                enabled,
                json.dumps(extra),
            )
        return row["id"] if row else 0

    async def list_channel_configs(
        self, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List channel configurations."""
        if project_id is not None:
            query = (
                "SELECT id, project_id, channel_type, enabled, config, "
                "created_at, updated_at FROM notification_channels "
                "WHERE project_id = $1 ORDER BY created_at"
            )
            params: tuple[Any, ...] = (project_id,)
        else:
            query = (
                "SELECT id, project_id, channel_type, enabled, config, "
                "created_at, updated_at FROM notification_channels "
                "ORDER BY created_at"
            )
            params = ()

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        result = []
        for row in rows:
            cfg = json.loads(row["config"]) if row["config"] else {}
            # Decrypt sensitive fields
            for key in ("smtp_password", "webhook_url"):
                if key in cfg and cfg[key]:
                    cfg[key] = self._decrypt(cfg[key])
            entry = {
                "id": str(row["id"]),
                "project_id": row["project_id"],
                "channel_type": row["channel_type"],
                "enabled": row["enabled"],
                **cfg,
            }
            result.append(entry)
        return result

    async def delete_channel_config(self, config_id: int | str) -> bool:
        """Delete a channel config by id. Returns True if deleted."""
        async with self._pool.acquire() as conn:
            tag = await conn.execute(
                "DELETE FROM notification_channels WHERE id = $1",
                int(config_id),
            )
        return tag == "DELETE 1"

    # ------------------------------------------------------------------
    # Trigger routing
    # ------------------------------------------------------------------

    async def save_trigger_routing(
        self, config: dict[str, Any]
    ) -> int:
        """Upsert a trigger routing config. Returns the row id."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO notification_triggers "
                "(project_id, trigger, channel_type, enabled) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (project_id, trigger, channel_type) "
                "DO UPDATE SET enabled = EXCLUDED.enabled, "
                "created_at = NOW() "
                "RETURNING id",
                config.get("project_id"),
                config["trigger"],
                config["channel_type"],
                config.get("enabled", True),
            )
        return row["id"] if row else 0

    async def list_trigger_routing(
        self, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List trigger routing configs."""
        if project_id is not None:
            query = (
                "SELECT id, project_id, trigger, channel_type, enabled, "
                "created_at FROM notification_triggers "
                "WHERE project_id = $1 ORDER BY trigger, channel_type"
            )
            params: tuple[Any, ...] = (project_id,)
        else:
            query = (
                "SELECT id, project_id, trigger, channel_type, enabled, "
                "created_at FROM notification_triggers "
                "ORDER BY trigger, channel_type"
            )
            params = ()

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [
            {
                "id": str(row["id"]),
                "project_id": row["project_id"],
                "trigger": row["trigger"],
                "channel_type": row["channel_type"],
                "enabled": row["enabled"],
                "created_at": row["created_at"].isoformat()
                if row["created_at"]
                else None,
            }
            for row in rows
        ]

    async def delete_trigger_routing(self, trigger_id: int | str) -> bool:
        """Delete a trigger routing config by id. Returns True if deleted."""
        async with self._pool.acquire() as conn:
            tag = await conn.execute(
                "DELETE FROM notification_triggers WHERE id = $1",
                int(trigger_id),
            )
        return tag == "DELETE 1"
