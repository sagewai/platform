# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""DirectiveAuditWriter — dual emission to Postgres + OTel structured log."""
from __future__ import annotations

import logging
from typing import Any

import pytest

from sagewai.sealed.directives.audit import DirectiveAuditWriter


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def insert_directive_evaluation(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


@pytest.mark.asyncio
async def test_emit_writes_postgres_and_otel(caplog):
    store = _FakeStore()
    writer = DirectiveAuditWriter(store=store)
    with caplog.at_level(logging.INFO, logger="sagewai.sealed.directives.audit"):
        await writer.emit(
            event_type="directive.evaluated",
            run_id="r-1",
            project_id="p-1",
            workflow_name="wf",
            policy_id="cost-overrun-default",
            signal_kind="cost_overrun",
            severity="warning",
            details={"actual_cost_usd": 12.4},
        )

    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["event_type"] == "directive.evaluated"
    assert row["run_id"] == "r-1"
    assert row["details"] == {"actual_cost_usd": 12.4}

    assert any(
        "directive.evaluated" in record.getMessage() for record in caplog.records
    )


@pytest.mark.asyncio
async def test_emit_swallows_postgres_failure(caplog):
    class _BrokenStore:
        async def insert_directive_evaluation(self, **kwargs: Any) -> None:
            raise RuntimeError("postgres unreachable")

    writer = DirectiveAuditWriter(store=_BrokenStore())
    with caplog.at_level(logging.WARNING, logger="sagewai.sealed.directives.audit"):
        await writer.emit(
            event_type="directive.evaluated",
            run_id="r-1",
            project_id=None,
            workflow_name="wf",
            policy_id=None,
            signal_kind=None,
            severity=None,
            details={},
        )
    assert any("eval_persist_failed" in r.getMessage() for r in caplog.records)
