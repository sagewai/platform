# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""DirectiveAuditWriter — dual emission of directive events.

Pattern mirrors sagewai.sealed.audit.AuditWriter (Sealed-i).
Postgres failures degrade to OTel-only with a `eval_persist_failed`
log marker; never raise back into the worker hot path.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("sagewai.sealed.directives.audit")


class _StoreLike(Protocol):
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
    ) -> None: ...


class DirectiveAuditWriter:
    """Dual-emit: Postgres `directive_evaluations` + OTel structured log."""

    def __init__(self, store: _StoreLike) -> None:
        self._store = store

    async def emit(
        self,
        *,
        event_type: str,
        run_id: str,
        project_id: str | None,
        workflow_name: str,
        policy_id: str | None,
        signal_kind: str | None,
        severity: str | None,
        details: dict[str, Any],
        decision_id: str | None = None,
    ) -> None:
        # OTel structured log first — survives even if Postgres is unreachable.
        logger.info(
            "%s",
            event_type,
            extra={
                "decision_id": decision_id,
                "run_id": run_id,
                "project_id": project_id,
                "workflow_name": workflow_name,
                "policy_id": policy_id,
                "signal_kind": signal_kind,
                "severity": severity,
                "details": details,
            },
        )
        try:
            await self._store.insert_directive_evaluation(
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
        except Exception:  # noqa: BLE001
            logger.warning(
                "eval_persist_failed event_type=%s run_id=%s", event_type, run_id,
                exc_info=True,
            )
