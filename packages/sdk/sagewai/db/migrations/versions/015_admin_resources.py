# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""generic project-scoped admin_resources table (MT durability foundation)

Revision ID: 015_admin_resources
Revises: 014_tenant_connection
"""

import sqlalchemy as sa
from alembic import op

revision = "015_admin_resources"
down_revision = "014_tenant_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_resources",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("kind", "resource_id"),
    )
    op.create_index("ix_admin_resources_project_id", "admin_resources", ["project_id"])
    op.create_index("ix_admin_resources_kind", "admin_resources", ["kind"])
    # NULL-safe partial unique: one name per (kind, project) and one global name
    # per kind; unnamed rows coexist freely.
    op.create_index(
        "ux_admin_resources_kind_proj_name",
        "admin_resources",
        ["kind", "project_id", "name"],
        unique=True,
        sqlite_where=sa.text("name IS NOT NULL"),
        postgresql_where=sa.text("name IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("admin_resources")
