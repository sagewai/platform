# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PostgreSQL/SQLite-backed notification store using SQLAlchemy Core.

Persists notification history, channel configs, and trigger routing
to any SQLAlchemy-supported database (SQLite by default, PostgreSQL in
production).  Replaces the former asyncpg-only implementation.

Usage::

    from sagewai.notifications.postgres_store import PostgresNotificationStore

    # Default (process-wide SQLite / SAGEWAI_DATABASE_URL engine):
    store = PostgresNotificationStore()
    await store.init()
    await store.record(notification)

    # Explicit engine:
    store = PostgresNotificationStore(engine=my_engine)

Back-compat note
----------------
The old constructor accepted a single positional ``pool`` argument and an
optional ``encryption_key``.  The new constructor accepts:

* ``engine=`` — a pre-built :class:`AsyncEngine` (highest priority)
* ``database_url=`` — passed to :func:`sagewai.db.engine.create_engine`
* ``pool`` — **ignored**; accepted so existing call sites don't raise
  ``TypeError``

``encryption_key`` — when supplied, secret fields (``smtp_password``,
``webhook_url``) in channel configs are encrypted at rest using Fernet
symmetric encryption.  Without a key those fields are stored in plaintext.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.engine import create_engine
from sagewai.db.models import (
    Base,
    NotificationChannelModel,
    NotificationHistoryModel,
    NotificationTriggerModel,
)
from sagewai.notifications.models import NotificationRecord

logger = logging.getLogger(__name__)


def _make_engine(
    database_url: str | None,
    pool: Any,
    engine: AsyncEngine | None,
) -> AsyncEngine:
    """Resolve the engine from the three possible constructor inputs."""
    if engine is not None:
        return engine
    if database_url is not None:
        return create_engine(database_url)
    # pool is ignored (asyncpg back-compat)
    return factory.get_engine()


class PostgresNotificationStore:
    """Notification store using SQLAlchemy Core — SQLite (default) or PostgreSQL.

    Parameters
    ----------
    database_url:
        Connection string passed to :func:`sagewai.db.engine.create_engine`.
        Ignored when *engine* is supplied.
    pool:
        Accepted for backwards-compatibility with callers that previously
        passed an asyncpg pool.  It is **not used** by this implementation.
    engine:
        Pre-built :class:`AsyncEngine`.  When supplied, *database_url* and
        *pool* are ignored.
    encryption_key:
        Accepted for backwards-compatibility; not used by this implementation.
    """

    def __init__(
        self,
        database_url: str | None = None,
        pool: Any = None,  # back-compat; not used
        *,
        engine: AsyncEngine | None = None,
        encryption_key: str | None = None,
    ) -> None:
        self._engine = _make_engine(database_url, pool, engine)
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

    async def init(self) -> None:
        """Bootstrap the schema when using SQLite; no-op on PostgreSQL (Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    # keep old name as alias for callers that used initialize()
    async def initialize(self) -> None:
        await self.init()

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def record(self, notification: NotificationRecord) -> None:
        """Insert a notification record into history (append-only)."""
        tbl = NotificationHistoryModel.__table__
        values = {
            "project_id": notification.project_id or "default",
            "trigger": notification.trigger,
            "title": notification.title,
            "body": notification.body,
            "severity": notification.severity,
            "agent_name": notification.agent_name,
            "channel_type": notification.channel_type,
            "delivered": notification.delivered,
            "error": notification.error,
            "created_at": notification.created_at,
        }
        async with self._engine.begin() as conn:
            await conn.execute(tbl.insert().values(**values))

    async def list_history(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        trigger: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query notification history with optional filtering, newest first."""
        tbl = NotificationHistoryModel.__table__
        stmt = select(tbl)
        if trigger is not None:
            stmt = stmt.where(tbl.c.trigger == trigger)
        if project_id is not None:
            stmt = stmt.where(tbl.c.project_id == project_id)
        stmt = stmt.order_by(tbl.c.created_at.desc()).limit(limit).offset(offset)

        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()

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
                "created_at": _to_iso(row["created_at"]),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Channel configs
    # ------------------------------------------------------------------

    async def save_channel_config(
        self, config: dict[str, Any]
    ) -> int:
        """Upsert a channel config. Returns the row id.

        Conflict target: (project_id, channel_type) — the unique constraint
        defined in migration 001 on notification_channels.
        """
        project_id = config.get("project_id") or "default"
        channel_type = config.get("channel_type", "")
        enabled = config.get("enabled", True)

        # Encrypt sensitive fields before storing
        cfg = dict(config)
        for key in ("smtp_password", "webhook_url"):
            if key in cfg and cfg[key]:
                cfg[key] = self._encrypt(cfg[key])

        # Store non-core fields in the config JSON column
        core_keys = {"project_id", "channel_type", "enabled", "id"}
        extra: dict[str, Any] = {
            k: v for k, v in cfg.items() if k not in core_keys
        }

        now = datetime.now(timezone.utc)
        tbl = NotificationChannelModel.__table__
        values = {
            "project_id": project_id,
            "channel_type": channel_type,
            "enabled": enabled,
            "config": extra,
            "created_at": now,
            "updated_at": now,
        }
        stmt = upsert(
            tbl,
            values,
            index_elements=["project_id", "channel_type"],
            set_={
                "enabled": enabled,
                "config": extra,
                "updated_at": now,
            },
            dialect=self._engine.dialect.name,
        )
        # RETURNING id — use insert().returning() via the upsert helper result
        # The portable way: execute, then SELECT the row back.
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
            row = (
                await conn.execute(
                    select(tbl.c.id).where(
                        tbl.c.project_id == project_id,
                        tbl.c.channel_type == channel_type,
                    )
                )
            ).scalar_one()
        return row

    async def list_channel_configs(
        self, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List channel configurations."""
        tbl = NotificationChannelModel.__table__
        stmt = select(tbl)
        if project_id is not None:
            stmt = stmt.where(tbl.c.project_id == project_id)
        stmt = stmt.order_by(tbl.c.created_at)

        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()

        result = []
        for row in rows:
            cfg = row["config"]
            if isinstance(cfg, str):
                cfg = json.loads(cfg) if cfg else {}
            if cfg is None:
                cfg = {}
            # Decrypt sensitive fields on read
            for key in ("smtp_password", "webhook_url"):
                if key in cfg and cfg[key]:
                    cfg[key] = self._decrypt(cfg[key])
            entry: dict[str, Any] = {
                "id": str(row["id"]),
                "project_id": row["project_id"],
                "channel_type": row["channel_type"],
                "enabled": row["enabled"],
                **cfg,
            }
            result.append(entry)
        return result

    async def delete_channel_config(self, config_id: int | str) -> bool:
        """Delete a channel config by id. Returns True if a row was deleted."""
        tbl = NotificationChannelModel.__table__
        stmt = sa_delete(tbl).where(tbl.c.id == int(config_id))
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount == 1

    # ------------------------------------------------------------------
    # Trigger routing
    # ------------------------------------------------------------------

    async def save_trigger_routing(
        self, config: dict[str, Any]
    ) -> int:
        """Upsert a trigger routing config. Returns the row id.

        Conflict target: (project_id, trigger, channel_type) — the unique
        constraint defined in migration 001 on notification_triggers.
        """
        project_id = config.get("project_id") or "default"
        trigger = config["trigger"]
        channel_type = config["channel_type"]
        enabled = config.get("enabled", True)

        now = datetime.now(timezone.utc)
        tbl = NotificationTriggerModel.__table__
        values = {
            "project_id": project_id,
            "trigger": trigger,
            "channel_type": channel_type,
            "enabled": enabled,
            "created_at": now,
        }
        stmt = upsert(
            tbl,
            values,
            index_elements=["project_id", "trigger", "channel_type"],
            set_={
                "enabled": enabled,
            },
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
            row = (
                await conn.execute(
                    select(tbl.c.id).where(
                        tbl.c.project_id == project_id,
                        tbl.c.trigger == trigger,
                        tbl.c.channel_type == channel_type,
                    )
                )
            ).scalar_one()
        return row

    async def list_trigger_routing(
        self, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List trigger routing configs."""
        tbl = NotificationTriggerModel.__table__
        stmt = select(tbl)
        if project_id is not None:
            stmt = stmt.where(tbl.c.project_id == project_id)
        stmt = stmt.order_by(tbl.c.trigger, tbl.c.channel_type)

        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()

        return [
            {
                "id": str(row["id"]),
                "project_id": row["project_id"],
                "trigger": row["trigger"],
                "channel_type": row["channel_type"],
                "enabled": row["enabled"],
                "created_at": _to_iso(row["created_at"]),
            }
            for row in rows
        ]

    async def delete_trigger_routing(self, trigger_id: int | str) -> bool:
        """Delete a trigger routing config by id. Returns True if a row was deleted."""
        tbl = NotificationTriggerModel.__table__
        stmt = sa_delete(tbl).where(tbl.c.id == int(trigger_id))
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount == 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_iso(value: Any) -> str | None:
    """Convert a datetime (or None) to an ISO 8601 string with timezone."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)
