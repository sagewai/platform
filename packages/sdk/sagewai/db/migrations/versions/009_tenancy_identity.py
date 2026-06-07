# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Multi-tenancy / RBAC identity schema (W0).

Creates the tenancy tables: org, user_account, project, membership,
invitation, user_session. One org (shared umbrella) -> many isolated
projects; tenant-scoped resources carry project_id (NULL = org-shared).

Key integrity (see the W0 RFC):
- partial unique indexes (NULL-safe) for org-shared vs project-scoped names
  and for one-org-membership-per-user;
- composite FKs (org_id, project_id) -> project(org_id, id) so a row can't
  claim one org while pointing at another org's project;
- CHECK constraints tying project_id nullability to the role namespace
  (org:* <-> NULL, project:* <-> NOT NULL).

SQLite gets this schema from Base.metadata.create_all (the models mirror this
DDL); Alembic runs only on Postgres.

Revision ID: 009_tenancy_identity
Revises: 008_directives
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "009_tenancy_identity"
down_revision: str | None = "008_directives"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_SCOPE_CHECK = (
    "(project_id IS NULL AND role IN ('org:owner','org:admin','org:member')) OR "
    "(project_id IS NOT NULL AND role IN ('project:admin','project:member','project:viewer'))"
)


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    empty_obj = sa.text("'{}'::jsonb") if is_pg else sa.text("'{}'")

    op.create_table(
        "org",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("contact_email", sa.Text(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False, server_default=sa.text("'UTC'")),
        sa.Column(
            "settings",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=empty_obj,
        ),
        sa.Column("master_key_ref", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "user_account",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("org.id"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("password_salt", sa.Text(), nullable=True),
        sa.Column("oidc_sub", sa.Text(), nullable=True),
        sa.Column("oidc_provider", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("org_id", "id", name="uq_user_account_org_id"),
    )
    op.create_index("ux_user_account_org_email", "user_account", ["org_id", "email"], unique=True)

    op.create_table(
        "project",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("org.id"), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False, server_default=sa.text("'production'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "settings",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=empty_obj,
        ),
        sa.Column("data_key_ref", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("org_id", "id", name="uq_project_org_id"),
    )
    op.create_index("ux_project_org_slug", "project", ["org_id", "slug"], unique=True)

    op.create_table(
        "membership",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["user_account.org_id", "user_account.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["project.org_id", "project.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_ROLE_SCOPE_CHECK, name="ck_membership_role_scope"),
    )
    op.create_index(
        "ux_membership_org",
        "membership",
        ["user_id", "org_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )
    op.create_index(
        "ux_membership_proj",
        "membership",
        ["user_id", "org_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )

    op.create_table(
        "invitation",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_by", sa.Text(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "invited_by"], ["user_account.org_id", "user_account.id"]
        ),
        sa.ForeignKeyConstraint(["org_id", "project_id"], ["project.org_id", "project.id"]),
        sa.CheckConstraint(_ROLE_SCOPE_CHECK, name="ck_invitation_role_scope"),
    )
    op.create_index("ux_invitation_token_hash", "invitation", ["token_hash"], unique=True)

    op.create_table(
        "user_session",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["user_account.org_id", "user_account.id"],
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_session")
    op.drop_index("ux_invitation_token_hash", table_name="invitation")
    op.drop_table("invitation")
    op.drop_index("ux_membership_proj", table_name="membership")
    op.drop_index("ux_membership_org", table_name="membership")
    op.drop_table("membership")
    op.drop_index("ux_project_org_slug", table_name="project")
    op.drop_table("project")
    op.drop_index("ux_user_account_org_email", table_name="user_account")
    op.drop_table("user_account")
    op.drop_table("org")
