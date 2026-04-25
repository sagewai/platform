# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""AuditWriter — dual-emit to Postgres + OTel structured log."""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("sagewai.sealed.audit")


class AuditWriter:
    """Single helper to write audit events to both:
        1. Postgres `sealed_audit_events` table (canonical, queryable)
        2. OTel structured log (streamable for Plan 3c Grafana row)

    One emit() call → both writes. Field names identical in both.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

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

        await self._store._pool.execute(
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
