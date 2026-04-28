# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Sealed-iii.C — replay snapshot columns on workflow_runs.

Spec: docs/superpowers/specs/2026-04-27-sealed-iii-c-replay-design.md
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006_replay_snapshots"
down_revision: str | None = "005_execution_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column("replay_of_run_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("replay_from_step", sa.Integer(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("code_hash", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_workflow_runs_replay_of",
        "workflow_runs",
        ["replay_of_run_id"],
        postgresql_where=sa.text("replay_of_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_workflow_runs_replay_of", table_name="workflow_runs"
    )
    op.drop_column("workflow_runs", "code_hash")
    op.drop_column("workflow_runs", "replay_from_step")
    op.drop_column("workflow_runs", "replay_of_run_id")
