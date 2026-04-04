# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Fleet audit events — structured logging for all fleet operations.

Every security-relevant fleet action (worker registration, enrollment key
usage, run claims, token lifecycle) is captured as a :class:`FleetAuditEvent`
and persisted through a pluggable :class:`FleetAuditBackend`.

Two backends ship out of the box:

* :class:`InMemoryFleetAuditBackend` — for testing and development.
* :class:`PostgresFleetAuditBackend` — production backend using the
  ``fleet_audit_events`` table (created by migration 005).

Usage::

    from sagewai.fleet.audit import (
        FleetAuditBackend,
        FleetAuditEvent,
        FleetAuditEventType,
        InMemoryFleetAuditBackend,
    )

    backend = InMemoryFleetAuditBackend()
    event = FleetAuditEvent(
        id="evt-1",
        org_id="acme",
        event_type=FleetAuditEventType.WORKER_REGISTERED,
        worker_id="w-42",
        details={"pool": "gpu"},
    )
    await backend.record(event)
    events = await backend.query("acme")
"""

from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Enum
# ------------------------------------------------------------------


class FleetAuditEventType(str, Enum):
    """All auditable fleet operations."""

    WORKER_REGISTERED = "worker.registered"
    WORKER_APPROVED = "worker.approved"
    WORKER_REJECTED = "worker.rejected"
    WORKER_REVOKED = "worker.revoked"
    WORKER_HEARTBEAT_MISSED = "worker.heartbeat_missed"
    ENROLLMENT_KEY_CREATED = "enrollment_key.created"
    ENROLLMENT_KEY_REVOKED = "enrollment_key.revoked"
    ENROLLMENT_KEY_USED = "enrollment_key.used"
    RUN_CLAIMED = "run.claimed"
    RUN_REPORTED = "run.reported"
    RUN_TIMEOUT = "run.timeout"
    TOKEN_ISSUED = "token.issued"
    TOKEN_REVOKED = "token.revoked"


# ------------------------------------------------------------------
# Event model
# ------------------------------------------------------------------


class FleetAuditEvent(BaseModel):
    """A single fleet audit event."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    org_id: str
    event_type: FleetAuditEventType
    worker_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ------------------------------------------------------------------
# Abstract backend
# ------------------------------------------------------------------


class FleetAuditBackend(ABC):
    """Abstract base for fleet audit storage."""

    @abstractmethod
    async def record(self, event: FleetAuditEvent) -> None:
        """Persist a single audit event."""

    @abstractmethod
    async def query(
        self,
        org_id: str,
        event_type: FleetAuditEventType | None = None,
        worker_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[FleetAuditEvent]:
        """Return events matching the given filters, newest first."""


# ------------------------------------------------------------------
# In-memory backend (testing / dev)
# ------------------------------------------------------------------


class InMemoryFleetAuditBackend(FleetAuditBackend):
    """In-memory backend for testing and development."""

    def __init__(self) -> None:
        self._events: list[FleetAuditEvent] = []

    async def record(self, event: FleetAuditEvent) -> None:
        self._events.append(event)

    async def query(
        self,
        org_id: str,
        event_type: FleetAuditEventType | None = None,
        worker_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[FleetAuditEvent]:
        results: list[FleetAuditEvent] = []
        for evt in self._events:
            if evt.org_id != org_id:
                continue
            if event_type is not None and evt.event_type != event_type:
                continue
            if worker_id is not None and evt.worker_id != worker_id:
                continue
            if since is not None and evt.created_at < since:
                continue
            results.append(evt)
        # Newest first
        results.sort(key=lambda e: e.created_at, reverse=True)
        return results[:limit]


# ------------------------------------------------------------------
# Postgres backend (production)
# ------------------------------------------------------------------


class PostgresFleetAuditBackend(FleetAuditBackend):
    """Postgres backend using the ``fleet_audit_events`` table.

    Requires an ``asyncpg`` connection pool.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def record(self, event: FleetAuditEvent) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO fleet_audit_events
                       (id, org_id, event_type, worker_id, details, created_at)
                   VALUES ($1, $2::text, $3::text, $4, $5::jsonb, $6)""",
                event.id,
                event.org_id,
                event.event_type.value,
                event.worker_id,
                json.dumps(event.details),
                event.created_at,
            )

    async def query(
        self,
        org_id: str,
        event_type: FleetAuditEventType | None = None,
        worker_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[FleetAuditEvent]:
        clauses = ["org_id = $1"]
        params: list[Any] = [org_id]
        idx = 2

        if event_type is not None:
            clauses.append(f"event_type = ${idx}")
            params.append(event_type.value)
            idx += 1

        if worker_id is not None:
            clauses.append(f"worker_id = ${idx}")
            params.append(worker_id)
            idx += 1

        if since is not None:
            clauses.append(f"created_at >= ${idx}")
            params.append(since)
            idx += 1

        where = " AND ".join(clauses)
        sql = (
            f"SELECT id, org_id, event_type, worker_id, details, created_at "
            f"FROM fleet_audit_events "
            f"WHERE {where} "
            f"ORDER BY created_at DESC "
            f"LIMIT ${idx}"
        )
        params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        events: list[FleetAuditEvent] = []
        for row in rows:
            details = row["details"]
            if isinstance(details, str):
                details = json.loads(details)
            events.append(
                FleetAuditEvent(
                    id=str(row["id"]),
                    org_id=row["org_id"],
                    event_type=FleetAuditEventType(row["event_type"]),
                    worker_id=row["worker_id"],
                    details=details or {},
                    created_at=row["created_at"],
                )
            )
        return events
