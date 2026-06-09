# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""tenant connection table

Revision ID: 014_tenant_connection
Revises: 013_tenant_run_telemetry
"""

import sqlalchemy as sa
from alembic import op

revision = "014_tenant_connection"
down_revision = "013_tenant_run_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connection",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("protocol", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("credentials_backend", sa.JSON(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("last_tested_at", sa.Text(), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.Column("protocol_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_connection_project_id", "connection", ["project_id"])
    op.create_index("ix_connection_protocol", "connection", ["protocol"])
    op.create_index(
        "ux_connection_global_name",
        "connection",
        ["protocol", "display_name"],
        unique=True,
        sqlite_where=sa.text("project_id IS NULL"),
        postgresql_where=sa.text("project_id IS NULL"),
    )
    op.create_index(
        "ux_connection_proj_name",
        "connection",
        ["project_id", "protocol", "display_name"],
        unique=True,
        sqlite_where=sa.text("project_id IS NOT NULL"),
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("connection")
