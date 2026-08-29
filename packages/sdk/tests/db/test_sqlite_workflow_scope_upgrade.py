# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Actual-startup upgrade for pre-026 SQLite workflow durability rows."""

from __future__ import annotations

import json
import sqlite3

import pytest

from sagewai.core.state import StepStatus, WorkflowRun
from sagewai.db import factory


def _create_legacy_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE workflow_runs ("
        "id TEXT PRIMARY KEY, "
        "project_id TEXT NOT NULL DEFAULT 'default', "
        "workflow_name TEXT NOT NULL, "
        "run_id TEXT NOT NULL, "
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


def _completed_run(*, project_id: str | None, run_id: str) -> WorkflowRun:
    run = WorkflowRun(workflow_name="durable", run_id=run_id, project_id=project_id)
    run.status = StepStatus.COMPLETED
    return run


@pytest.mark.asyncio
async def test_startup_upgrades_and_reloads_completed_legacy_checkpoint(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    factory.reset_engine()
    db = tmp_path / "home" / "db" / "sagewai.db"
    db.parent.mkdir(parents=True)
    legacy = _completed_run(project_id="alpha", run_id="done")
    connection = sqlite3.connect(db)
    _create_legacy_tables(connection)
    connection.execute(
        "INSERT INTO workflow_runs "
        "(id, project_id, workflow_name, run_id, status, data, idempotency_key) "
        "VALUES (?, 'default', 'durable', 'done', 'completed', ?, 'once')",
        ("durable:done", json.dumps(legacy.to_dict())),
    )
    connection.execute(
        "INSERT INTO workflow_events (project_id, run_id, event_type) "
        "VALUES ('default', 'done', 'COMPLETED')"
    )
    connection.commit()
    connection.close()

    store = await factory.get_workflow_store()
    loaded = await store.load_run("durable", "done", project_id="alpha")
    assert loaded is not None
    assert loaded.status == StepStatus.COMPLETED
    assert await store.load_run("durable", "done", project_id="default") is None

    connection = sqlite3.connect(db)
    assert connection.execute(
        "SELECT id, project_id, idempotency_key FROM workflow_runs"
    ).fetchone() == ("7:p:alpha7:durabledone", "alpha", "7:p:alphaonce")
    assert connection.execute(
        "SELECT project_id FROM workflow_events WHERE run_id = 'done'"
    ).fetchone() == ("alpha",)
    for table_name in ("workflow_runs", "workflow_events"):
        project_column = next(
            row
            for row in connection.execute(f"PRAGMA table_info({table_name})")
            if row[1] == "project_id"
        )
        assert project_column[3] == 0
        assert project_column[4] is None
    connection.close()

    await factory.dispose_engine()
    store = await factory.get_workflow_store()
    reloaded = await store.load_run("durable", "done", project_id="alpha")
    assert reloaded is not None and reloaded.status == StepStatus.COMPLETED

    global_run = _completed_run(project_id=None, run_id="global")
    await store.save_run(global_run)
    await factory.dispose_engine()
    store = await factory.get_workflow_store()
    global_reloaded = await store.load_run("durable", "global", project_id=None)
    assert global_reloaded is not None
    assert global_reloaded.project_id is None
    await factory.dispose_engine()


@pytest.mark.asyncio
async def test_startup_scope_upgrade_is_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    factory.reset_engine()
    db = tmp_path / "home" / "db" / "sagewai.db"
    db.parent.mkdir(parents=True)
    legacy = _completed_run(project_id="alpha", run_id="done")
    connection = sqlite3.connect(db)
    _create_legacy_tables(connection)
    connection.execute(
        "INSERT INTO workflow_runs "
        "(id, project_id, workflow_name, run_id, status, data, idempotency_key) "
        "VALUES ('legacy', 'default', 'durable', 'done', 'completed', ?, 'once')",
        (json.dumps(legacy.to_dict()),),
    )
    connection.commit()
    connection.close()

    await factory.ensure_schema()
    await factory.ensure_schema()

    connection = sqlite3.connect(db)
    assert connection.execute("SELECT id, idempotency_key FROM workflow_runs").fetchone() == (
        "7:p:alpha7:durabledone",
        "7:p:alphaonce",
    )
    connection.close()
    await factory.dispose_engine()


@pytest.mark.asyncio
async def test_startup_refuses_ambiguous_event_scope_transactionally(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    factory.reset_engine()
    db = tmp_path / "home" / "db" / "sagewai.db"
    db.parent.mkdir(parents=True)
    connection = sqlite3.connect(db)
    _create_legacy_tables(connection)
    for project_id in ("alpha", "beta"):
        run = _completed_run(project_id=project_id, run_id="same")
        connection.execute(
            "INSERT INTO workflow_runs "
            "(id, project_id, workflow_name, run_id, status, data) "
            "VALUES (?, 'default', 'durable', 'same', 'completed', ?)",
            (project_id, json.dumps(run.to_dict())),
        )
    connection.execute(
        "INSERT INTO workflow_events (project_id, run_id, event_type) "
        "VALUES ('default', 'same', 'COMPLETED')"
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        RuntimeError,
        match="cannot migrate workflow events with ambiguous run project scope",
    ):
        await factory.ensure_schema()

    connection = sqlite3.connect(db)
    assert connection.execute(
        "SELECT id, project_id FROM workflow_runs ORDER BY id"
    ).fetchall() == [("alpha", "default"), ("beta", "default")]
    connection.close()
    await factory.dispose_engine()
