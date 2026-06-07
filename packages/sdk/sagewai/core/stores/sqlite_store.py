# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SQLite-backed WorkflowStore using SQLAlchemy Core + aiosqlite.

Durable drop-in replacement for InMemoryStore that persists workflow
checkpoints to SQLite. Implements exactly the 5-method WorkflowStore ABC.

Queue, fleet, worker-registry, and revocation operations are NOT implemented
here — they remain Postgres-only (SAGEWAI_DATABASE_URL-gated in serve.py).

The store deliberately omits a ``_pool`` attribute so that
``_build_revocation_registry`` in state.py correctly degrades to None.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.core.context import resolve_project_id
from sagewai.core.state import StepStatus, WorkflowRun, WorkflowStore
from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.models import Base, WorkflowRunModel

logger = logging.getLogger(__name__)

_TABLE = WorkflowRunModel.__table__


def _id(pid: str, workflow_name: str, run_id: str) -> str:
    """Return the project-qualified primary key for a workflow run row.

    Format: ``<project_id>:<workflow_name>:<run_id>``.
    Internal to this store — callers outside this module must not construct
    or parse this key.
    """
    return f"{pid}:{workflow_name}:{run_id}"


class SqliteWorkflowStore(WorkflowStore):
    """SQLite-backed WorkflowStore.

    Parameters
    ----------
    engine:
        Optional AsyncEngine. Falls back to ``factory.get_engine()`` which
        returns (or creates) the process-wide SQLite engine under
        ``SAGEWAI_HOME``.

    Notes
    -----
    No ``_pool`` attribute is defined intentionally — ``_build_revocation_registry``
    in ``core/state.py`` checks ``hasattr(store, "_pool")`` and returns ``None``
    when the attribute is absent, which is the correct degraded behaviour for
    SQLite (revocation requires asyncpg).
    """

    def __init__(self, engine: AsyncEngine | None = None) -> None:
        self._engine: AsyncEngine = engine or factory.get_engine()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Bootstrap the schema via create_all (idempotent; SQLite only).

        On a Postgres engine this is a no-op — Alembic owns the schema there.
        In tests (and on first use) this ensures all tables exist before any
        reads or writes.
        """
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """No-op — the factory owns the engine lifecycle."""

    # ------------------------------------------------------------------
    # WorkflowStore ABC — the 5 required methods
    # ------------------------------------------------------------------

    async def save_run(self, run: WorkflowRun) -> None:
        """Upsert the workflow run into ``workflow_runs``."""
        pid = resolve_project_id(run.project_id)
        key = _id(pid, run.workflow_name, run.run_id)

        values: dict[str, Any] = {
            # Primary key / identity
            "id": key,
            "workflow_name": run.workflow_name,
            "run_id": run.run_id,
            "status": run.status.value,
            # Full serialised run document — JSONType handles serialisation
            "data": run.to_dict(),
            # Project scoping
            "project_id": pid,
            # Timestamps — always refresh updated_at on every save
            "updated_at": datetime.now(timezone.utc),
            # Typed sandbox / execution columns
            "execution_mode": run.execution_mode.value,
            "requires_sandbox_mode": run.requires_sandbox_mode.value,
            "requires_image": run.requires_image,
            "requires_variant": (
                run.requires_variant.value if run.requires_variant else None
            ),
            "requires_network_policy": run.requires_network_policy.value,
            # Sealed / security columns
            "security_profile_ref": run.security_profile_ref,
            # ArrayText columns — pass Python lists directly
            "effective_env_keys": list(run.effective_env_keys),
            "effective_secret_keys": list(run.effective_secret_keys),
            # Revocation
            "revoked_at": run.revoked_at,
            "revoke_reason": run.revoke_reason,
            # Replay / artifact columns
            "artifact_destination": (
                run.artifact_destination.model_dump(mode="json")
                if run.artifact_destination
                else None
            ),
            "replay_of_run_id": run.replay_of_run_id,
            "replay_from_step": run.replay_from_step,
            "code_hash": run.code_hash,
            # Directive chain — list of dicts, JSONType handles serialisation
            "directive_chain": [
                e.model_dump(mode="json") for e in run.directive_chain
            ],
            "estimated_cost_usd": run.estimated_cost_usd,
            "replay_re_evaluate_directives": run.replay_re_evaluate_directives,
            "execution_mode_override": (
                run.execution_mode_override.value
                if run.execution_mode_override
                else None
            ),
            "identity_from": run.identity_from,
            # NOT-NULL columns with model-level defaults — supply explicit values
            # to avoid relying on server_default (not evaluated on Core upserts).
            "input": {},
            "steps_completed": 0,
            "input_encrypted": False,
            "output_encrypted": False,
        }

        stmt = upsert(
            _TABLE,
            values,
            index_elements=["id"],
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def load_run(self, workflow_name: str, run_id: str) -> WorkflowRun | None:
        """Load a single workflow run by name and ID."""
        pid = resolve_project_id()
        key = _id(pid, workflow_name, run_id)
        stmt = select(_TABLE.c.data).where(_TABLE.c.id == key)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).fetchone()
        if row is None:
            return None
        data = row[0]
        if isinstance(data, str):
            data = json.loads(data)
        return WorkflowRun.from_dict(data)

    async def list_runs(
        self,
        workflow_name: str,
        status: StepStatus | None = None,
    ) -> list[WorkflowRun]:
        """List runs for a workflow, optionally filtered by status."""
        pid = resolve_project_id()
        stmt = (
            select(_TABLE.c.data)
            .where(_TABLE.c.workflow_name == workflow_name)
            .where(_TABLE.c.project_id == pid)
            .order_by(_TABLE.c.created_at.desc())
        )
        if status is not None:
            stmt = stmt.where(_TABLE.c.status == status.value)

        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).fetchall()

        results = []
        for row in rows:
            data = row[0]
            if isinstance(data, str):
                data = json.loads(data)
            results.append(WorkflowRun.from_dict(data))
        return results

    async def recover_stale_runs(
        self, stale_timeout_seconds: int = 300
    ) -> list[WorkflowRun]:
        """Return RUNNING runs that haven't had a heartbeat within the timeout."""
        pid = resolve_project_id()
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_timeout_seconds)
        stmt = (
            select(_TABLE.c.data)
            .where(_TABLE.c.status == StepStatus.RUNNING.value)
            .where(_TABLE.c.project_id == pid)
            .where(_TABLE.c.updated_at < cutoff)
            .order_by(_TABLE.c.updated_at.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).fetchall()

        results = []
        for row in rows:
            data = row[0]
            if isinstance(data, str):
                data = json.loads(data)
            results.append(WorkflowRun.from_dict(data))
        return results

    async def heartbeat(self, workflow_name: str, run_id: str) -> None:
        """Refresh updated_at to prevent stale detection."""
        pid = resolve_project_id()
        key = _id(pid, workflow_name, run_id)
        stmt = (
            update(_TABLE)
            .where(_TABLE.c.id == key)
            .values(updated_at=datetime.now(timezone.utc))
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
