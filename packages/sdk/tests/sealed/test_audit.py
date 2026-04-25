# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for AuditWriter dual-emit."""
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.sealed.audit import AuditWriter


@pytest.mark.asyncio
async def test_emit_writes_to_postgres_and_log(caplog):
    fake_pool = MagicMock()
    fake_pool.execute = AsyncMock()
    fake_store = MagicMock()
    fake_store._pool = fake_pool

    writer = AuditWriter(fake_store)
    with caplog.at_level(logging.INFO, logger="sagewai.sealed.audit"):
        await writer.emit(
            event_type="profile.created",
            profile_id="acme",
            details={"key_count": 4},
        )

    fake_pool.execute.assert_awaited_once()
    args = fake_pool.execute.await_args.args
    assert "INSERT INTO sealed_audit_events" in args[0]
    assert args[1] == "profile.created"
    assert args[4] == "acme"  # profile_id

    # Log emitted with structured extras
    log_records = [r for r in caplog.records if "sealed.profile.created" in r.message]
    assert len(log_records) == 1


@pytest.mark.asyncio
async def test_emit_merges_context_into_details():
    fake_pool = MagicMock()
    fake_pool.execute = AsyncMock()
    fake_store = MagicMock()
    fake_store._pool = fake_pool

    writer = AuditWriter(fake_store)
    await writer.emit(
        event_type="profile.cascade.resolved",
        profile_id="acme",
        details={"resolved_keys": ["A", "B"]},
        context={"workflow_name": "billing"},
    )

    args = fake_pool.execute.await_args.args
    persisted_details = json.loads(args[8])  # details JSONB column
    assert persisted_details["workflow_name"] == "billing"
    assert persisted_details["resolved_keys"] == ["A", "B"]
