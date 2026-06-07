# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""AuditWriter — dual-emit to DB + OTel structured log.

Constructor variants
--------------------
``AuditWriter(engine=engine)``
    Preferred form. Uses SQLAlchemy Core to write to ``sealed_audit_events``
    via the provided ``AsyncEngine``. Works on both SQLite and PostgreSQL.
    When *engine* is omitted, defaults to ``factory.get_engine()``.

``AuditWriter(store)`` — back-compat positional form
    Accepted so that legacy callers (``PostgresStore``, ``core.state``,
    ``core.worker``, ``admin.revocation_routes``, ``cli.sealed``) need no
    changes.  Resolution order:

    1. If *store* has a ``._pool`` attribute (raw asyncpg pool — this is the
       untouched ``PostgresStore``), keep using the raw asyncpg INSERT path so
       that ``recover_revoked_stuck_runs`` and ``sealed_audit_cleanup`` on the
       pg-only store continue to work exactly as before.

    2. If *store* has a ``._engine`` attribute (SQLAlchemy-backed store such as
       ``SqliteWorkflowStore``), extract the engine and use the Core path.

    3. Otherwise (e.g. ``InMemoryStore`` in tests / dry-run paths), fall back to
       ``factory.get_engine()`` and use the Core path.  Emit is best-effort in
       this case and will only persist if the factory engine points at a real DB.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("sagewai.sealed.audit")

# Sentinel so we can distinguish "no positional arg" from explicit None.
_MISSING = object()


class AuditWriter:
    """Single helper to write audit events to both:
        1. DB ``sealed_audit_events`` table (canonical, queryable)
        2. OTel structured log (streamable for Plan 3c Grafana row)

    One emit() call → both writes. Field names identical in both.

    When ``default_redactor`` is provided, ``details`` and ``context``
    payloads pass through ``Redactor.redact_dict()`` before write. This
    is defense-in-depth — Sealed-i forbids putting secret values in
    audit details, but this layer enforces the rule rather than trusting
    every caller.
    """

    def __init__(
        self,
        store: Any = _MISSING,
        *,
        default_redactor: Any | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._redactor = default_redactor
        # Resolve the write backend.
        if engine is not None:
            # Explicit engine= kwarg wins.
            self._engine: AsyncEngine | None = engine
            self._pool: Any = None
        elif store is _MISSING or store is None:
            # No store provided — use factory engine (Core path).
            self._engine = None  # resolved lazily to avoid circular imports
            self._pool = None
        elif hasattr(store, "_pool"):
            # PostgresStore (untouched asyncpg store) — keep raw asyncpg path.
            self._pool = store._pool
            self._engine = None
        elif hasattr(store, "_engine"):
            # SQLAlchemy-backed store (e.g. SqliteWorkflowStore).
            self._engine = store._engine
            self._pool = None
        else:
            # Unknown store shape — fall back to factory engine.
            self._engine = None
            self._pool = None

    def _get_engine(self) -> AsyncEngine:
        """Lazily resolve factory engine (avoids import cycle at module load)."""
        if self._engine is not None:
            return self._engine
        from sagewai.db import factory
        return factory.get_engine()

    async def emit(
        self,
        *,
        event_type: str,
        actor_type: str = "system",
        actor_id: str | None = None,
        profile_id: str | None = None,
        secret_key: str | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
        details: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        merged_details = {**(context or {}), **(details or {})}
        if self._redactor is not None and merged_details:
            merged_details, _matched = self._redactor.redact_dict(merged_details)

        if self._pool is not None:
            # Raw asyncpg path — preserved for untouched PostgresStore callers.
            await self._pool.execute(
                """
                INSERT INTO sealed_audit_events
                  (event_type, actor_type, actor_id, profile_id, secret_key,
                   run_id, project_id, details)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                """,
                event_type,
                actor_type,
                actor_id,
                profile_id,
                secret_key,
                run_id,
                project_id,
                json.dumps(merged_details),
            )
        else:
            # SQLAlchemy Core path — works on both SQLite and PostgreSQL.
            from sagewai.db.models import SealedAuditEventModel

            tbl = SealedAuditEventModel.__table__
            async with self._get_engine().begin() as conn:
                await conn.execute(
                    tbl.insert().values(
                        event_type=event_type,
                        actor_type=actor_type,
                        actor_id=actor_id,
                        profile_id=profile_id,
                        secret_key=secret_key,
                        run_id=run_id,
                        project_id=project_id,
                        details=merged_details,
                    )
                )

        logger.info(
            f"sagewai.sealed.{event_type}",
            extra={
                "sagewai.event": f"sagewai.sealed.{event_type}",
                "sagewai.actor_type": actor_type,
                "sagewai.actor_id": actor_id,
                "sagewai.profile_id": profile_id,
                "sagewai.secret_key": secret_key,
                "sagewai.run_id": run_id,
                "sagewai.project_id": project_id,
                "sagewai.details": merged_details,
            },
        )
