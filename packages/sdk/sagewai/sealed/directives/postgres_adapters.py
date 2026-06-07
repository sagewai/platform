# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SQLAlchemy Core adapters for directive_evaluations + pending_directive_approvals.

Both adapters are engine-only (SQLAlchemy Core) and accept an optional
``engine=`` keyword argument (defaults to ``factory.get_engine()``).

Constructor:
    DirectiveEvaluationsAdapter(engine=engine)
    ApprovalsPostgresAdapter(engine=engine)
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine


class DirectiveEvaluationsAdapter:
    """Writes/queries directive_evaluations rows via SQLAlchemy Core.

    Parameters
    ----------
    engine:
        SQLAlchemy async engine.  Defaults to ``factory.get_engine()``.
    database_url:
        Convenience alternative to *engine*: creates an engine from the URL.
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine | None = None,
        database_url: str | None = None,
    ) -> None:
        if engine is not None:
            self._engine: AsyncEngine | None = engine
        elif database_url is not None:
            from sagewai.db.engine import create_engine
            self._engine = create_engine(database_url)
        else:
            self._engine = None

    def _get_engine(self) -> AsyncEngine:
        if self._engine is not None:
            return self._engine
        from sagewai.db import factory
        return factory.get_engine()

    async def insert_directive_evaluation(
        self,
        *,
        event_type: str,
        decision_id: str | None,
        run_id: str,
        project_id: str | None,
        workflow_name: str,
        policy_id: str | None,
        signal_kind: str | None,
        severity: str | None,
        details: dict[str, Any],
    ) -> None:
        from sagewai.db.models import DirectiveEvaluationModel
        tbl = DirectiveEvaluationModel.__table__
        async with self._get_engine().begin() as conn:
            await conn.execute(
                tbl.insert().values(
                    event_type=event_type,
                    decision_id=decision_id,
                    run_id=run_id,
                    project_id=project_id,
                    workflow_name=workflow_name,
                    policy_id=policy_id,
                    signal_kind=signal_kind,
                    severity=severity,
                    details=details,
                )
            )

    async def list_for_run(self, *, run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        from sagewai.db.models import DirectiveEvaluationModel
        tbl = DirectiveEvaluationModel.__table__
        stmt = (
            select(tbl)
            .where(tbl.c.run_id == run_id)
            .order_by(tbl.c.created_at.desc())
            .limit(limit)
        )
        async with self._get_engine().connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [self._row_to_dict(r) for r in rows]

    async def list_filtered(
        self,
        *,
        run_id: str | None = None,
        policy_id: str | None = None,
        event_type: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        from sagewai.db.models import DirectiveEvaluationModel
        tbl = DirectiveEvaluationModel.__table__
        stmt = select(tbl).order_by(tbl.c.created_at.desc()).limit(limit)
        if run_id:
            stmt = stmt.where(tbl.c.run_id == run_id)
        if policy_id:
            stmt = stmt.where(tbl.c.policy_id == policy_id)
        if event_type:
            stmt = stmt.where(tbl.c.event_type == event_type)
        async with self._get_engine().connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        details = row["details"]
        if isinstance(details, str):
            details = json.loads(details)
        return {
            "id": row["id"],
            "event_type": row["event_type"],
            "decision_id": row["decision_id"],
            "run_id": row["run_id"],
            "project_id": row["project_id"],
            "workflow_name": row["workflow_name"],
            "policy_id": row["policy_id"],
            "signal_kind": row["signal_kind"],
            "severity": row["severity"],
            "details": details,
            "created_at": row["created_at"],
        }


class ApprovalsPostgresAdapter:
    """SQLAlchemy Core adapter for pending_directive_approvals.

    Parameters
    ----------
    engine:
        SQLAlchemy async engine.  Defaults to ``factory.get_engine()``.
    database_url:
        Convenience alternative to *engine*: creates an engine from the URL.
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine | None = None,
        database_url: str | None = None,
    ) -> None:
        if engine is not None:
            self._engine: AsyncEngine | None = engine
        elif database_url is not None:
            from sagewai.db.engine import create_engine
            self._engine = create_engine(database_url)
        else:
            self._engine = None

    def _get_engine(self) -> AsyncEngine:
        if self._engine is not None:
            return self._engine
        from sagewai.db import factory
        return factory.get_engine()

    async def fetch_one_pending_for(self, run_id: str, policy_id: str) -> dict[str, Any] | None:
        from sagewai.db.models import PendingDirectiveApprovalModel
        tbl = PendingDirectiveApprovalModel.__table__
        stmt = (
            select(tbl)
            .where(
                tbl.c.run_id == run_id,
                tbl.c.policy_id == policy_id,
                tbl.c.status == "pending",
            )
            .limit(1)
        )
        async with self._get_engine().connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        if row is None:
            return None
        return {
            "decision_id": row["decision_id"],
            "run_id": row["run_id"],
            "policy_id": row["policy_id"],
            "status": row["status"],
            "expires_at": row["expires_at"],
        }

    async def insert(self, row: dict[str, Any]) -> None:
        from sagewai.db.models import PendingDirectiveApprovalModel
        tbl = PendingDirectiveApprovalModel.__table__
        async with self._get_engine().begin() as conn:
            await conn.execute(
                tbl.insert().values(
                    decision_id=row["decision_id"],
                    run_id=row["run_id"],
                    project_id=row.get("project_id"),
                    workflow_name=row["workflow_name"],
                    policy_id=row["policy_id"],
                    triggering_signal=row["triggering_signal"],
                    proposed_action=row["proposed_action"],
                    requested_at=row.get("requested_at"),
                    status=row.get("status", "pending"),
                    decided_at=row.get("decided_at"),
                    decided_by=row.get("decided_by"),
                    operator_note=row.get("operator_note"),
                    expires_at=row["expires_at"],
                )
            )

    async def update_status(
        self,
        *,
        decision_id: str,
        status: str,
        decided_at: datetime | None = None,
        decided_by: str | None = None,
        operator_note: str | None = None,
    ) -> dict[str, Any]:
        from sagewai.db.models import PendingDirectiveApprovalModel
        tbl = PendingDirectiveApprovalModel.__table__
        stmt = (
            update(tbl)
            .where(tbl.c.decision_id == decision_id)
            .values(
                status=status,
                decided_at=decided_at,
                decided_by=decided_by,
                operator_note=operator_note,
            )
            .returning(tbl.c.decision_id, tbl.c.run_id, tbl.c.status, tbl.c.decided_at)
        )
        async with self._get_engine().begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        if row is None:
            raise KeyError(decision_id)
        return dict(row)

    async def fetch_approved_for_run(self, run_id: str) -> list[dict[str, Any]]:
        from sagewai.db.models import PendingDirectiveApprovalModel
        tbl = PendingDirectiveApprovalModel.__table__
        stmt = (
            select(
                tbl.c.decision_id,
                tbl.c.policy_id,
                tbl.c.proposed_action,
                tbl.c.decided_at,
            )
            .where(tbl.c.run_id == run_id, tbl.c.status == "approved")
            .order_by(tbl.c.decided_at.asc())
        )
        async with self._get_engine().connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [
            {
                "decision_id": r["decision_id"],
                "policy_id": r["policy_id"],
                "proposed_action": (
                    json.loads(r["proposed_action"])
                    if isinstance(r["proposed_action"], str)
                    else r["proposed_action"]
                ),
                "decided_at": r["decided_at"],
            }
            for r in rows
        ]
