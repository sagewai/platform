# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Postgres bindings for directive_evaluations + pending_directive_approvals."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


class DirectiveEvaluationsAdapter:
    """Writes/queries directive_evaluations rows."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

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
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO directive_evaluations
                    (event_type, decision_id, run_id, project_id, workflow_name,
                     policy_id, signal_kind, severity, details)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                """,
                event_type, decision_id, run_id, project_id, workflow_name,
                policy_id, signal_kind, severity, json.dumps(details),
            )

    async def list_for_run(self, *, run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, event_type, decision_id, run_id, project_id,
                       workflow_name, policy_id, signal_kind, severity,
                       details, created_at
                FROM directive_evaluations
                WHERE run_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                run_id, limit,
            )
            return [
                {
                    "id": r["id"],
                    "event_type": r["event_type"],
                    "decision_id": r["decision_id"],
                    "run_id": r["run_id"],
                    "project_id": r["project_id"],
                    "workflow_name": r["workflow_name"],
                    "policy_id": r["policy_id"],
                    "signal_kind": r["signal_kind"],
                    "severity": r["severity"],
                    "details": json.loads(r["details"]) if isinstance(r["details"], str) else r["details"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    async def list_filtered(
        self,
        *,
        run_id: str | None = None,
        policy_id: str | None = None,
        event_type: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = []
        args: list[Any] = []
        if run_id:
            args.append(run_id)
            clauses.append(f"run_id = ${len(args)}")
        if policy_id:
            args.append(policy_id)
            clauses.append(f"policy_id = ${len(args)}")
        if event_type:
            args.append(event_type)
            clauses.append(f"event_type = ${len(args)}")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        args.append(limit)
        sql = f"""
            SELECT id, event_type, decision_id, run_id, project_id,
                   workflow_name, policy_id, signal_kind, severity,
                   details, created_at
            FROM directive_evaluations
            {where}
            ORDER BY created_at DESC
            LIMIT ${len(args)}
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            out = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("details"), str):
                    d["details"] = json.loads(d["details"])
                out.append(d)
            return out


class ApprovalsPostgresAdapter:
    """Pool adapter for PendingApprovalsRegistry."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def fetch_one_pending_for(self, run_id: str, policy_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT decision_id, run_id, policy_id, status, expires_at
                FROM pending_directive_approvals
                WHERE run_id = $1 AND policy_id = $2 AND status = 'pending'
                LIMIT 1
                """,
                run_id, policy_id,
            )
            return dict(row) if row else None

    async def insert(self, row: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pending_directive_approvals
                    (decision_id, run_id, project_id, workflow_name, policy_id,
                     triggering_signal, proposed_action, requested_at, status,
                     decided_at, decided_by, operator_note, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb,
                        $8, $9, $10, $11, $12, $13)
                """,
                row["decision_id"], row["run_id"], row["project_id"],
                row["workflow_name"], row["policy_id"],
                json.dumps(row["triggering_signal"]),
                json.dumps(row["proposed_action"]),
                row["requested_at"], row["status"],
                row["decided_at"], row["decided_by"], row["operator_note"],
                row["expires_at"],
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
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE pending_directive_approvals
                SET status = $2, decided_at = $3, decided_by = $4, operator_note = $5
                WHERE decision_id = $1
                RETURNING decision_id, run_id, status, decided_at
                """,
                decision_id, status, decided_at, decided_by, operator_note,
            )
            if row is None:
                raise KeyError(decision_id)
            return dict(row)

    async def fetch_approved_for_run(self, run_id: str) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT decision_id, policy_id, proposed_action, decided_at
                FROM pending_directive_approvals
                WHERE run_id = $1 AND status = 'approved'
                ORDER BY decided_at ASC
                """,
                run_id,
            )
            return [
                {
                    "decision_id": r["decision_id"],
                    "policy_id": r["policy_id"],
                    "proposed_action": json.loads(r["proposed_action"])
                    if isinstance(r["proposed_action"], str)
                    else r["proposed_action"],
                    "decided_at": r["decided_at"],
                }
                for r in rows
            ]
