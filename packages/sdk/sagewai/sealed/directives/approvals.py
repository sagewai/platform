# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PendingApprovalsRegistry — Postgres-backed HITL queue.

See spec §6.1.

Status transitions: pending → approved | denied | expired → consumed (terminal).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sagewai.sealed.directives.models import DirectiveDecision


class SuppressedAlreadyPendingError(RuntimeError):
    """Raised when (run_id, policy_id) already has a pending approval."""


class AlreadyDecidedError(RuntimeError):
    """Raised when approving/denying an approval that is no longer pending."""


class _Pool(Protocol):
    async def fetch_one_pending_for(self, run_id: str, policy_id: str) -> Any | None: ...
    async def insert(self, row: dict[str, Any]) -> None: ...
    async def update_status(self, *, decision_id: str, **fields: Any) -> Any: ...
    async def fetch_approved_for_run(self, run_id: str) -> list[Any]: ...


class PendingApprovalsRegistry:
    """In-memory-or-Postgres registry; table operations live behind the Pool Protocol."""

    def __init__(self, pool: _Pool) -> None:
        self._pool = pool
        self._all_rows: list[dict] = []  # only for list_pending() in unit tests

    async def request(
        self,
        *,
        decision: DirectiveDecision,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        sig = decision.triggering_signal
        existing = await self._pool.fetch_one_pending_for(
            sig.run_id, decision.directive_policy_id,
        )
        if existing is not None:
            raise SuppressedAlreadyPendingError(
                f"already pending for ({sig.run_id}, {decision.directive_policy_id})"
            )
        now = datetime.now(tz=timezone.utc)
        row = {
            "decision_id": decision.decision_id,
            "run_id": sig.run_id,
            "project_id": sig.project_id,
            "workflow_name": sig.workflow_name,
            "policy_id": decision.directive_policy_id,
            "triggering_signal": sig.model_dump(mode="json"),
            "proposed_action": decision.action.model_dump(mode="json"),
            "requested_at": now,
            "status": "pending",
            "decided_at": None,
            "decided_by": None,
            "operator_note": None,
            "expires_at": now + timedelta(seconds=ttl_seconds),
        }
        await self._pool.insert(row)
        self._all_rows.append(row)
        return row

    async def approve(
        self,
        *,
        decision_id: str,
        actor: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        return await self._transition(
            decision_id=decision_id,
            from_status="pending",
            to_status="approved",
            actor=actor,
            note=note,
        )

    async def deny(
        self,
        *,
        decision_id: str,
        actor: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        return await self._transition(
            decision_id=decision_id,
            from_status="pending",
            to_status="denied",
            actor=actor,
            note=note,
        )

    async def mark_consumed(self, *, decision_id: str) -> dict[str, Any]:
        return await self._transition(
            decision_id=decision_id,
            from_status="approved",
            to_status="consumed",
            actor=None,
            note=None,
        )

    async def expire_overdue(self, *, now: datetime) -> int:
        count = 0
        for row in self._all_rows:
            if row["status"] == "pending" and row["expires_at"] < now:
                await self._pool.update_status(
                    decision_id=row["decision_id"],
                    status="expired",
                    decided_at=now,
                )
                count += 1
        return count

    async def list_pending(self) -> list[dict[str, Any]]:
        return [r for r in self._all_rows if r["status"] == "pending"]

    async def list_approved_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return await self._pool.fetch_approved_for_run(run_id)

    async def _transition(
        self,
        *,
        decision_id: str,
        from_status: str,
        to_status: str,
        actor: str | None,
        note: str | None,
    ) -> dict[str, Any]:
        for row in self._all_rows:
            if row["decision_id"] == decision_id:
                if row["status"] != from_status:
                    raise AlreadyDecidedError(
                        f"decision {decision_id} is {row['status']}, not {from_status}"
                    )
                break
        else:
            raise KeyError(decision_id)
        return await self._pool.update_status(
            decision_id=decision_id,
            status=to_status,
            decided_at=datetime.now(tz=timezone.utc),
            decided_by=actor,
            operator_note=note,
        )
