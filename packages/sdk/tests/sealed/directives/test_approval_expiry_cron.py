from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_expire_overdue_approvals_cron_marks_old_rows():
    from sagewai.admin.cron import expire_overdue_directive_approvals

    expired: list[str] = []

    class _Adapter:
        async def expire_overdue(self, *, now: datetime) -> int:
            # Pretend two rows are overdue.
            expired.extend(["dec-1", "dec-2"])
            return 2

    audit_events: list[dict] = []

    class _Audit:
        async def emit(self, **kwargs):
            audit_events.append(kwargs)

    count = await expire_overdue_directive_approvals(
        adapter=_Adapter(), audit=_Audit(), now=datetime.now(tz=timezone.utc),
    )
    assert count == 2
    assert any(e["event_type"] == "directive.approval_expired" for e in audit_events)
