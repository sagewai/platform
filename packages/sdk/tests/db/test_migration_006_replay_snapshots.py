# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Smoke test for migration 006 — validates revision constants + DDL shape."""
from __future__ import annotations

import importlib

import sqlalchemy as sa


def test_006_module_imports_with_correct_revisions():
    mod = importlib.import_module(
        "sagewai.db.migrations.versions.006_replay_snapshots"
    )
    assert mod.revision == "006_replay_snapshots"
    assert mod.down_revision == "005_execution_mode"


def test_006_upgrade_emits_expected_sql_against_sqlite():
    """Compile the upgrade ops against an in-memory SQLite engine to
    validate column types + index structure without needing Postgres.
    The partial-index `WHERE` is Postgres-specific and is skipped by
    SQLite — that's expected; we only assert the columns land."""
    mod = importlib.import_module(
        "sagewai.db.migrations.versions.006_replay_snapshots"
    )
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "workflow_runs",
        metadata,
        sa.Column("run_id", sa.Text(), primary_key=True),
    )
    metadata.create_all(engine)

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()
            cols = {
                c["name"] for c in sa.inspect(conn).get_columns("workflow_runs")
            }
            assert {"replay_of_run_id", "replay_from_step", "code_hash"} <= cols

            mod.downgrade()
            cols = {
                c["name"] for c in sa.inspect(conn).get_columns("workflow_runs")
            }
            assert "replay_of_run_id" not in cols
            assert "replay_from_step" not in cols
            assert "code_hash" not in cols
