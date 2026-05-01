# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Smoke test for migration 008 — validates revision constants + DDL shape."""
from __future__ import annotations

import importlib

import sqlalchemy as sa


def test_007_merge_module_imports():
    mod = importlib.import_module(
        "sagewai.db.migrations.versions.007_merge_006_branches"
    )
    assert mod.revision == "007_merge_006_branches"
    # Merge has TWO down_revisions — both 006 heads.
    assert set(mod.down_revision) == {"006_artifact_destination", "006_replay_snapshots"}


def test_008_module_imports_with_correct_revisions():
    mod = importlib.import_module(
        "sagewai.db.migrations.versions.008_directives"
    )
    assert mod.revision == "008_directives"
    assert mod.down_revision == "007_merge_006_branches"


def test_008_upgrade_emits_expected_columns_against_sqlite():
    mod = importlib.import_module(
        "sagewai.db.migrations.versions.008_directives"
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
            assert {
                "directive_chain",
                "estimated_cost_usd",
                "replay_re_evaluate_directives",
                "execution_mode_override",
                "identity_from",
            } <= cols
            tables = sa.inspect(conn).get_table_names()
            assert "directive_evaluations" in tables
            assert "pending_directive_approvals" in tables

            eval_cols = {
                c["name"] for c in sa.inspect(conn).get_columns("directive_evaluations")
            }
            assert {"event_type", "decision_id", "details", "created_at"} <= eval_cols

            approval_cols = {
                c["name"] for c in sa.inspect(conn).get_columns("pending_directive_approvals")
            }
            assert {
                "decision_id", "status", "expires_at", "triggering_signal", "proposed_action"
            } <= approval_cols

            mod.downgrade()
            cols = {
                c["name"] for c in sa.inspect(conn).get_columns("workflow_runs")
            }
            for new_col in (
                "directive_chain",
                "estimated_cost_usd",
                "replay_re_evaluate_directives",
                "execution_mode_override",
                "identity_from",
            ):
                assert new_col not in cols
            tables = sa.inspect(conn).get_table_names()
            assert "directive_evaluations" not in tables
            assert "pending_directive_approvals" not in tables
