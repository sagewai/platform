# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Dependency and schema-preservation contracts for SQLite migration 026."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap

import pytest


def test_default_sqlite_startup_does_not_import_optional_alembic(tmp_path) -> None:
    script = textwrap.dedent(
        """
        import asyncio
        import importlib.abc
        import sys

        class BlockAlembic(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname == "alembic" or fullname.startswith("alembic."):
                    raise ModuleNotFoundError("alembic blocked by dependency contract")
                return None

        sys.meta_path.insert(0, BlockAlembic())
        from sagewai.db import factory

        asyncio.run(factory.ensure_schema())
        assert not any(name == "alembic" or name.startswith("alembic.") for name in sys.modules)
        asyncio.run(factory.dispose_engine())
        """
    )
    environment = os.environ.copy()
    environment["SAGEWAI_HOME"] = str(tmp_path / "plain-install-home")
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_scope_rebuild_preserves_constraints_indexes_and_autoincrement(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    from sagewai.db import factory

    factory.reset_engine()
    db = tmp_path / "home" / "db" / "sagewai.db"
    db.parent.mkdir(parents=True)
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE workflow_runs ("
        "id TEXT PRIMARY KEY, "
        "project_id TEXT NOT NULL DEFAULT 'default', "
        "workflow_name TEXT NOT NULL, "
        "run_id TEXT NOT NULL CHECK(length(run_id) > 0), "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "data JSON NOT NULL DEFAULT '{}', "
        "idempotency_key TEXT)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX idx_workflow_runs_idempotency_key "
        "ON workflow_runs(idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    connection.execute(
        "CREATE TABLE workflow_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "project_id TEXT NOT NULL DEFAULT 'default', "
        "run_id TEXT NOT NULL, "
        "event_type TEXT NOT NULL, "
        "data JSON NOT NULL DEFAULT '{}', "
        "created_at TEXT)"
    )
    connection.execute("CREATE INDEX idx_workflow_events_type ON workflow_events(event_type)")
    connection.execute(
        "INSERT INTO workflow_runs "
        "(id, project_id, workflow_name, run_id, status, data, idempotency_key) "
        "VALUES ('legacy', 'default', 'wf', 'run', 'completed', ?, 'once')",
        (json.dumps({"workflow_name": "wf", "run_id": "run", "project_id": "alpha"}),),
    )
    connection.commit()
    connection.close()

    await factory.ensure_schema()

    connection = sqlite3.connect(db)
    run_table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='workflow_runs'"
    ).fetchone()[0]
    event_table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='workflow_events'"
    ).fetchone()[0]
    indexes = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='index' AND tbl_name IN ('workflow_runs', 'workflow_events')"
        )
    }
    assert "CHECK (length(run_id) > 0)" in run_table_sql
    assert "AUTOINCREMENT" in event_table_sql
    assert "WHERE idempotency_key IS NOT NULL" in indexes["idx_workflow_runs_idempotency_key"]
    assert "idx_workflow_events_type" in indexes
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO workflow_runs "
            "(id, project_id, workflow_name, run_id, status, data) "
            "VALUES ('invalid', 'alpha', 'wf', '', 'pending', '{}')"
        )
    connection.close()
    await factory.dispose_engine()
