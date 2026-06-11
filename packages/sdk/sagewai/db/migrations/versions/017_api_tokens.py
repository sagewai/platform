# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""tenant-scoped api_token table (machine/CI auth for multi-tenant mode)

Revision ID: 017_api_tokens
Revises: 016_rate_limits
"""

import sqlalchemy as sa
from alembic import op

revision = "017_api_tokens"
down_revision = "016_rate_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_token",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("subject_user_id", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # Auth lookup is by token_hash (pre-context); globally unique.
    op.create_index("ux_api_token_hash", "api_token", ["token_hash"], unique=True)
    op.create_index("ix_api_token_org_project", "api_token", ["org_id", "project_id"])
    # NULL-safe partial-unique on name: one org-shared name, one name per project.
    op.create_index(
        "ux_api_token_org_name",
        "api_token",
        ["org_id", "name"],
        unique=True,
        sqlite_where=sa.text("project_id IS NULL AND name IS NOT NULL"),
        postgresql_where=sa.text("project_id IS NULL AND name IS NOT NULL"),
    )
    op.create_index(
        "ux_api_token_org_proj_name",
        "api_token",
        ["org_id", "project_id", "name"],
        unique=True,
        sqlite_where=sa.text("project_id IS NOT NULL AND name IS NOT NULL"),
        postgresql_where=sa.text("project_id IS NOT NULL AND name IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("api_token")
