# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""task coordinator tables

Revision ID: 028_task_coordinator
Revises: 027_fleet_task_project_scope
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "028_task_coordinator"
down_revision = "027_fleet_task_project_scope"
branch_labels = None
depends_on = None

TABLES = (
    "tasks", "task_events", "task_feed", "task_commands", "task_spend",
    "task_repository_leases", "task_defaults", "task_triggers", "work_activity",
)


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _ts(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.func.now(),
    )


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("project_scope_key", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("profile", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("board_column", sa.Text(), nullable=False),
        sa.Column("attention_owner", sa.Text(), nullable=True),
        sa.Column("waiting_reason", sa.Text(), nullable=True),
        sa.Column("current_cycle", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_gate", sa.Text(), nullable=True),
        sa.Column("pending_questions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_material_questions", sa.Integer(), nullable=False, server_default="0"),
        _ts("next_run_at", nullable=True),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_epoch", sa.Integer(), nullable=False, server_default="0"),
        _ts("lease_expires_at", nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("budget_used", _json(), nullable=False),
        sa.Column("task_json", _json(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("project_scope_key", "task_id", name="pk_tasks"),
    )
    op.create_index("ix_tasks_scope_status", "tasks", ["project_scope_key", "status"])
    op.create_index("ix_tasks_scope_lease", "tasks", ["project_scope_key", "lease_expires_at"])

    op.create_table(
        "task_events",
        sa.Column("project_scope_key", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_ref", sa.Text(), nullable=True),
        sa.Column("payload_json", _json(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("project_scope_key", "id", name="pk_task_events"),
        sa.UniqueConstraint("project_scope_key", "task_id", "sequence", name="uq_task_events_task_sequence"),
    )

    op.create_table(
        "task_feed",
        sa.Column("project_scope_key", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("feed_sequence", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload_json", _json(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("project_scope_key", "task_id", "feed_sequence", name="pk_task_feed"),
    )

    op.create_table(
        "task_commands",
        sa.Column("project_scope_key", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("payload_json", _json(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("project_scope_key", "task_id", "command_id", name="pk_task_commands"),
    )

    op.create_table(
        "task_spend",
        sa.Column("project_scope_key", sa.Text(), nullable=False),
        sa.Column("reservation_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("runtime", sa.Text(), nullable=False),
        sa.Column("usd_reserved", sa.Text(), nullable=False),
        sa.Column("usd_actual", sa.Text(), nullable=True),
        sa.Column("unknown", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.Text(), nullable=False, server_default="reserved"),
        _ts("created_at"),
        _ts("settled_at", nullable=True),
        sa.PrimaryKeyConstraint("project_scope_key", "reservation_id", name="pk_task_spend"),
    )
    op.create_index("ix_task_spend_scope_task_cycle", "task_spend", ["project_scope_key", "task_id", "cycle"])

    op.create_table(
        "task_repository_leases",
        sa.Column("project_scope_key", sa.Text(), nullable=False),
        sa.Column("lease_key", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("work_id", sa.Text(), nullable=True),
        _ts("acquired_at"),
        _ts("expires_at", nullable=True),
        sa.PrimaryKeyConstraint("project_scope_key", "lease_key", name="pk_task_repository_leases"),
    )

    op.create_table(
        "task_defaults",
        sa.Column("project_scope_key", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("defaults_json", _json(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("project_scope_key", name="pk_task_defaults"),
    )

    op.create_table(
        "task_triggers",
        sa.Column("project_scope_key", sa.Text(), nullable=False),
        sa.Column("trigger_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("spec_json", _json(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("project_scope_key", "trigger_id", name="pk_task_triggers"),
    )

    op.create_table(
        "work_activity",
        sa.Column("project_scope_key", sa.Text(), nullable=False),
        sa.Column("work_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_json", _json(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("project_scope_key", "work_id", "run_id", "sequence", name="pk_work_activity"),
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
