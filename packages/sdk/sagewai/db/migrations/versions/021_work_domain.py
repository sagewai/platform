# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""generic Work domain and append-only event store

Revision ID: 021_work_domain
Revises: 020_fleet_task_lease
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "021_work_domain"
down_revision = "020_fleet_task_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column("work_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("profile", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=True),
        sa.Column("active_run_id", sa.Text(), nullable=True),
        sa.Column("pending_gate", sa.Text(), nullable=True),
        sa.Column("profile_context", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_work_items_project_id", "work_items", ["project_id"])

    op.create_table(
        "work_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("work_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_ref", sa.Text(), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "work_id",
            "sequence",
            name="uq_work_events_work_sequence",
        ),
    )
    op.create_index(
        "ix_work_events_project_work_sequence",
        "work_events",
        ["project_id", "work_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_events_project_work_sequence", table_name="work_events")
    op.drop_table("work_events")
    op.drop_index("ix_work_items_project_id", table_name="work_items")
    op.drop_table("work_items")
