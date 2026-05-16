# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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
