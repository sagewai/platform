# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Sealed-v — directive_evaluations + pending_directive_approvals tables
+ workflow_runs columns for directive_chain, estimated_cost_usd,
replay_re_evaluate_directives, execution_mode_override, identity_from.

Spec: docs/superpowers/specs/2026-04-29-sealed-v-reactive-directives-design.md
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008_directives"
down_revision: str | None = "007_merge_006_branches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    _empty_obj = sa.text("'{}'::jsonb") if is_pg else sa.text("'{}'")
    _empty_arr = sa.text("'[]'::jsonb") if is_pg else sa.text("'[]'")

    op.create_table(
        "directive_evaluations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("decision_id", sa.Text(), nullable=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("workflow_name", sa.Text(), nullable=False),
        sa.Column("policy_id", sa.Text(), nullable=True),
        sa.Column("signal_kind", sa.Text(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=True),
        sa.Column(
            "details",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=_empty_obj,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "idx_directive_eval_recent",
        "directive_evaluations",
        ["event_type", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_directive_eval_run",
        "directive_evaluations",
        ["run_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_directive_eval_decision",
        "directive_evaluations",
        ["decision_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("decision_id IS NOT NULL"),
    )

    op.create_table(
        "pending_directive_approvals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("decision_id", sa.Text(), nullable=False, unique=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("workflow_name", sa.Text(), nullable=False),
        sa.Column("policy_id", sa.Text(), nullable=False),
        sa.Column(
            "triggering_signal",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "proposed_action",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("operator_note", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_directive_approvals_pending",
        "pending_directive_approvals",
        ["status", "requested_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "idx_directive_approvals_run",
        "pending_directive_approvals",
        ["run_id", sa.text("requested_at DESC")],
    )

    op.add_column(
        "workflow_runs",
        sa.Column(
            "directive_chain",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=_empty_arr,
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "replay_re_evaluate_directives",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("execution_mode_override", sa.Text(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("identity_from", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "identity_from")
    op.drop_column("workflow_runs", "execution_mode_override")
    op.drop_column("workflow_runs", "replay_re_evaluate_directives")
    op.drop_column("workflow_runs", "estimated_cost_usd")
    op.drop_column("workflow_runs", "directive_chain")
    op.drop_index(
        "idx_directive_approvals_run", table_name="pending_directive_approvals"
    )
    op.drop_index(
        "idx_directive_approvals_pending", table_name="pending_directive_approvals"
    )
    op.drop_table("pending_directive_approvals")
    op.drop_index("idx_directive_eval_decision", table_name="directive_evaluations")
    op.drop_index("idx_directive_eval_run", table_name="directive_evaluations")
    op.drop_index("idx_directive_eval_recent", table_name="directive_evaluations")
    op.drop_table("directive_evaluations")
