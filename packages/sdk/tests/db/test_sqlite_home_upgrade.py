# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SQLite home in-place upgrade.

A home created before a column-adding migration has tables that `create_all` won't
ALTER, so a fail-closed startup probe would hard-fail. `ensure_schema()` must add the
missing columns + indexes so an older local home boots transparently.
"""
from __future__ import annotations

import sqlite3

import pytest

# An older fleet_tasks: pre-020 (no lease_expires_at/attempts) AND missing the `pool`
# column, so the upgrade must add a NOT NULL TEXT DEFAULT 'default' — the exact case the
# old hand-rolled renderer broke on (it emitted an invalid `DEFAULT default`).
_PRE_020_FLEET_TASKS = """
CREATE TABLE fleet_tasks (
  run_id TEXT PRIMARY KEY, org_id TEXT NOT NULL, project_id TEXT, model TEXT,
  labels TEXT NOT NULL DEFAULT '{}', payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending', worker_id TEXT, claimed_at TIMESTAMP,
  output TEXT, error TEXT, reported_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)
"""


@pytest.mark.asyncio
async def test_ensure_schema_upgrades_pre_020_fleet_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    from sagewai import home
    from sagewai.db import factory

    factory.reset_engine()
    dbdir = home.db_dir()
    dbdir.mkdir(parents=True, exist_ok=True)
    db = dbdir / "sagewai.db"
    con = sqlite3.connect(str(db))
    con.execute(_PRE_020_FLEET_TASKS)
    con.execute("INSERT INTO fleet_tasks (run_id, org_id, status) VALUES ('old','o','completed')")
    con.commit()
    con.close()

    await factory.ensure_schema()  # the gateway-boot path

    cols = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(fleet_tasks)")}
    idx = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA index_list(fleet_tasks)")}
    assert "lease_expires_at" in cols and "attempts" in cols  # B2 lease columns added
    assert "pool" in cols                                      # NOT NULL TEXT DEFAULT 'default' added
    assert "ix_fleet_tasks_lease" in idx                       # index added
    # existing data preserved; NOT NULL columns backfilled to their (correctly-rendered)
    # defaults — a string `'default'` and an int `0`, not the old invalid `DEFAULT default`.
    assert sqlite3.connect(str(db)).execute(
        "SELECT pool, attempts FROM fleet_tasks WHERE run_id='old'"
    ).fetchone() == ("default", 0)

    # the fail-closed probe now passes + the store is usable on the upgraded home
    from sagewai.fleet.task_store import PostgresTaskStore

    store = PostgresTaskStore(engine=factory.get_engine())
    await store.init()  # must not raise
    await store.enqueue({"run_id": "r1", "org_id": "o", "project_id": None, "pool": "default",
                         "model": "m", "labels": {}, "payload": {"k": 1}})
    claimed = await store.claim_task("w", "o", ["m"], "default", {}, project_id=None)
    assert claimed and claimed["run_id"] == "r1"
    await factory.dispose_engine()


@pytest.mark.asyncio
async def test_upgrade_adds_func_default_column_via_fallback(tmp_path, monkeypatch):
    """A column whose default SQLite's ALTER can't take (func.now() -> CURRENT_TIMESTAMP,
    an expression default) is still added as a plain nullable column, so it *exists* — a
    missing column would crash a non-probed table later, which is worse than a lost default.
    """
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    from sagewai import home
    from sagewai.db import factory

    factory.reset_engine()
    dbdir = home.db_dir()
    dbdir.mkdir(parents=True, exist_ok=True)
    db = dbdir / "sagewai.db"
    con = sqlite3.connect(str(db))
    # fleet_tasks missing created_at (model server_default=func.now()) + the lease columns
    con.execute(
        "CREATE TABLE fleet_tasks (run_id TEXT PRIMARY KEY, org_id TEXT NOT NULL, "
        "project_id TEXT, pool TEXT NOT NULL DEFAULT 'default', model TEXT, "
        "labels TEXT NOT NULL DEFAULT '{}', payload TEXT NOT NULL DEFAULT '{}', "
        "status TEXT NOT NULL DEFAULT 'pending', worker_id TEXT, claimed_at TIMESTAMP, "
        "output TEXT, error TEXT, reported_at TIMESTAMP)"
    )
    con.commit()
    con.close()

    await factory.ensure_schema()

    cols = {r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(fleet_tasks)")}
    assert "created_at" in cols  # added via the plain-nullable fallback
    assert "lease_expires_at" in cols and "attempts" in cols
    await factory.dispose_engine()


@pytest.mark.asyncio
async def test_ensure_schema_noop_on_fresh_home(tmp_path, monkeypatch):
    """A fresh home builds the current schema directly — the upgrade pass is a no-op."""
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "fresh"))
    from sagewai.db import factory

    factory.reset_engine()
    await factory.ensure_schema()
    await factory.ensure_schema()  # idempotent: a second call adds nothing
    from sagewai.fleet.task_store import PostgresTaskStore

    store = PostgresTaskStore(engine=factory.get_engine())
    await store.init()  # probe passes on a fresh home
    await factory.dispose_engine()
