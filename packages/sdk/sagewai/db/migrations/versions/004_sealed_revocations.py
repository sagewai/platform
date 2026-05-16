# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Sealed-iii.A — sealed_revocations table + workflow_runs.revoked_at columns.

Spec:        Sealed-iii.A (mid-run revocation API + pool reset hook)
Driving plan: Sealed-iii.A — revocation registry, fan-out, pool reset
PR:          #151

Summary
-------
Adds the ``sealed_revocations`` table with a unique partial index that
enforces "at most one active revocation per (profile_id, secret_key)"
plus a per-profile timeline index. Extends ``workflow_runs`` with
``revoked_at`` / ``revoke_reason`` so the worker's between-steps
revocation poll can abort and surface the cause to operators.

Revision ID: 004_sealed_revocations
Revises: 003_sealed
Create Date: 2026-04-25
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004_sealed_revocations"
down_revision: str | None = "003_sealed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sealed_revocations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("profile_id", sa.Text(), nullable=False),
        sa.Column("secret_key", sa.Text(), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("revoked_by", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "hard",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("lifted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lifted_by", sa.Text(), nullable=True),
    )
    # Unique partial index: at most one active revocation per (profile_id, secret_key)
    op.create_index(
        "idx_sealed_revocations_active",
        "sealed_revocations",
        ["profile_id", "secret_key"],
        unique=True,
        postgresql_where=sa.text("lifted_at IS NULL"),
    )
    op.create_index(
        "idx_sealed_revocations_profile",
        "sealed_revocations",
        ["profile_id", sa.text("revoked_at DESC")],
    )

    op.add_column(
        "workflow_runs",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("revoke_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "revoke_reason")
    op.drop_column("workflow_runs", "revoked_at")
    op.drop_index("idx_sealed_revocations_profile", table_name="sealed_revocations")
    op.drop_index("idx_sealed_revocations_active", table_name="sealed_revocations")
    op.drop_table("sealed_revocations")
