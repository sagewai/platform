# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sealed revoke / lift-revocation / list-revocations CLI."""
import json
import os

import pytest
from click.testing import CliRunner

from sagewai.cli.sealed import sealed_group

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_DATABASE_URL"),
    reason="SAGEWAI_DATABASE_URL not set",
)


def test_revoke_then_list_then_lift(monkeypatch):
    """Smoke test: revoke a key, see it in list, lift it."""
    runner = CliRunner()
    monkeypatch.setenv("SAGEWAI_DATABASE_URL", os.environ["SAGEWAI_DATABASE_URL"])

    # Use a unique profile id to avoid conflicts with existing data
    profile_id = "test-acme-cli-task13"
    secret_key = "TEST_KEY_CLI_TASK13"

    # Cleanup any prior state
    import asyncio

    from sagewai.core.stores.postgres import PostgresStore

    async def _cleanup():
        store = PostgresStore(database_url=os.environ["SAGEWAI_DATABASE_URL"])
        await store.initialize()
        try:
            await store._pool.execute(
                "DELETE FROM sealed_revocations WHERE profile_id = $1",
                profile_id,
            )
        finally:
            await store.close()

    asyncio.run(_cleanup())

    try:
        # revoke
        result = runner.invoke(
            sealed_group,
            ["revoke", profile_id, secret_key, "--reason", "cli test", "--yes"],
        )
        assert result.exit_code == 0, result.output
        assert "Revoked" in result.output

        # list-revocations
        result = runner.invoke(
            sealed_group,
            ["list-revocations", "--profile", profile_id],
        )
        assert result.exit_code == 0
        assert secret_key in result.output

        rows = json.loads(result.output)
        rid = next(r["id"] for r in rows if r["secret_key"] == secret_key)

        # lift
        result = runner.invoke(sealed_group, ["lift-revocation", str(rid), "--yes"])
        assert result.exit_code == 0
        assert "Lifted" in result.output
    finally:
        asyncio.run(_cleanup())
