# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""fleet B1: durable fleet_tasks queue

Revision ID: 019_fleet_tasks
Revises: 018_fleet_schema_correction
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "019_fleet_tasks"
down_revision = "018_fleet_schema_correction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fleet_tasks",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("pool", sa.Text(), nullable=False, server_default="default"),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("labels", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("run_id"),
        sa.CheckConstraint(
            "status IN ('pending','claimed','completed','failed')",
            name="ck_fleet_tasks_status",
        ),
    )
    op.create_index(
        "ix_fleet_tasks_claim", "fleet_tasks",
        ["status", "org_id", "project_id", "pool", "created_at"],
    )
    op.create_index(
        "ix_fleet_tasks_scope", "fleet_tasks", ["org_id", "project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("fleet_tasks")
