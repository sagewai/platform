# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
#
# SPDX-License-Identifier: AGPL-3.0-or-later
"""sagewai.admin.cron — daily maintenance functions for the admin/scheduler.

Functions here are designed to be called once per day by the operator's
scheduler (e.g. APScheduler, Kubernetes CronJob, or a simple asyncio loop).
They are intentionally kept side-effect-free apart from the injected
*adapter* and *audit* arguments so they can be unit-tested without a live
database.

Registered daily jobs (as of Sealed-v):
  - expire_overdue_directive_approvals  (Task 18)

TODO(Sealed-v Task 18 follow-up): wire expire_overdue_directive_approvals into
the same daily scheduler that invokes PostgresStore.sealed_audit_cleanup().
The Postgres store method is called directly from the operator's process; when
a centralised cron runner is introduced, register this function alongside it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


async def expire_overdue_directive_approvals(
    *,
    adapter: Any,  # PendingApprovalsRegistry or its pool adapter — must have expire_overdue(now=...)
    audit: Any,    # DirectiveAuditWriter-like with emit(**kwargs)
    now: datetime,
) -> int:
    """Expire any pending approvals past their expires_at timestamp.

    Returns the number of rows transitioned to 'expired'.
    Wired into the daily cron alongside Sealed-i's audit_retention_cleanup.
    """
    count = await adapter.expire_overdue(now=now)
    if count > 0:
        await audit.emit(
            event_type="directive.approval_expired",
            run_id="*cron*",
            project_id=None,
            workflow_name="*cron*",
            policy_id=None,
            signal_kind=None,
            severity=None,
            details={"expired_count": count},
        )
    return count
