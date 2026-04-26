# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sealed-i — sealed_audit_events table + workflow_runs cascade columns.

Spec:        Sealed-i (profile management foundation, the moat)
Driving plan: Sealed-i Task 7 — audit + cascade-resolution persistence
PR:          #150

Summary
-------
Creates the ``sealed_audit_events`` table with three indexes for the
common query patterns (recent events, per-profile timeline, per-run
trace) and adds the cascade-resolution result columns on
``workflow_runs``: ``security_profile_ref`` (the run's user-level
profile ref), ``effective_env_keys`` and ``effective_secret_keys``
(NAMES only — values stay in the sandbox per the trust-boundary
invariant in docs/architecture/runtime-topology.md).

Revision ID: 003_sealed
Revises: 002_sandbox_requirements
Create Date: 2026-04-25
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_sealed"
down_revision: str | None = "002_sandbox_requirements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sealed_audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("profile_id", sa.Text(), nullable=True),
        sa.Column("secret_key", sa.Text(), nullable=True),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_sealed_audit_recent",
        "sealed_audit_events",
        ["event_type", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_sealed_audit_profile",
        "sealed_audit_events",
        ["profile_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_sealed_audit_run",
        "sealed_audit_events",
        ["run_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )

    op.add_column(
        "workflow_runs",
        sa.Column("security_profile_ref", sa.Text(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "effective_env_keys",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "effective_secret_keys",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "effective_secret_keys")
    op.drop_column("workflow_runs", "effective_env_keys")
    op.drop_column("workflow_runs", "security_profile_ref")
    op.drop_index("idx_sealed_audit_run", table_name="sealed_audit_events")
    op.drop_index("idx_sealed_audit_profile", table_name="sealed_audit_events")
    op.drop_index("idx_sealed_audit_recent", table_name="sealed_audit_events")
    op.drop_table("sealed_audit_events")
