# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Sealed-iii.B end-to-end: redaction.match events land in Postgres,
no secret values land in details JSONB.

Gated on SAGEWAI_DATABASE_URL like the rest of the integration suite.
"""
from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_DATABASE_URL"),
    reason="requires SAGEWAI_DATABASE_URL to run integration tests",
)


@pytest.mark.asyncio
async def test_redaction_match_persists_no_value_in_details() -> None:
    import asyncpg

    from sagewai.sandbox.models import SandboxMode, ToolCall, ToolResult
    from sagewai.sandbox.redacting_handle import RedactingSandboxHandle
    from sagewai.sealed.audit import AuditWriter
    from sagewai.sealed.redaction import Redactor

    pool = await asyncpg.create_pool(os.environ["SAGEWAI_DATABASE_URL"])
    try:
        # AuditWriter expects a store with ._pool — we wrap the asyncpg pool
        class Store:
            def __init__(self, pool):
                self._pool = pool

        store = Store(pool)
        secrets = {"OPENAI_API_KEY": "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaa"}
        redactor = Redactor(secrets)
        audit = AuditWriter(store, default_redactor=redactor)

        class _H:
            sandbox_id = "sgw-it"
            mode = SandboxMode.PER_RUN
            image = "img"
            image_digest = "sha256:x"

            async def exec(self, c):
                return ToolResult(
                    call_id="c", ok=True,
                    stdout="leak sk-aaaaaaaaaaaaaaaaaaaaaaaaaaa here",
                )

            async def set_env(self, env): ...
            async def copy_in(self, src, dst): ...
            async def copy_out(self, src, dst): ...
            async def stats(self):
                from sagewai.sandbox.models import SandboxStats
                return SandboxStats()
            async def stop(self, *, timeout=10.0): ...

        wrapper = RedactingSandboxHandle(
            _H(),
            redactor=redactor,
            audit_writer=audit,
            run_id="r-itb-1",
            profile_id="p-it",
        )

        result = await wrapper.exec(ToolCall(tool="shell", args={}, call_id="c"))
        assert "<redacted:OPENAI_API_KEY>" in (result.stdout or "")

        # Query Postgres: redaction.match row must exist; details must NOT
        # contain the secret value as a substring anywhere.
        rows = await pool.fetch(
            "SELECT details FROM sealed_audit_events "
            "WHERE event_type='redaction.match' AND run_id=$1",
            "r-itb-1",
        )
        assert len(rows) >= 1
        for row in rows:
            details_str = (
                json.dumps(row["details"])
                if isinstance(row["details"], dict) else row["details"]
            )
            assert "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaa" not in str(details_str)
    finally:
        await pool.close()
