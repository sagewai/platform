# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""tenant provider table

Revision ID: 011_tenant_provider
Revises: 010_tenant_audit
"""
import sqlalchemy as sa
from alembic import op

revision = "011_tenant_provider"
down_revision = "010_tenant_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_provider_project_id", "provider", ["project_id"])
    op.create_index("ux_provider_global_name", "provider", ["provider_name"], unique=True,
                    sqlite_where=sa.text("project_id IS NULL"), postgresql_where=sa.text("project_id IS NULL"))
    op.create_index("ux_provider_proj_name", "provider", ["project_id", "provider_name"], unique=True,
                    sqlite_where=sa.text("project_id IS NOT NULL"), postgresql_where=sa.text("project_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_table("provider")
