# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Durable, hash-chained, per-tenant audit (W8).

Creates two tables:

- ``audit_event`` — a per-``(org_id, project_id)`` append-only hash chain
  (``project_id IS NULL`` = the org-level chain). ``seq`` is the event's
  position within its chain; ``hash = sha256(prev_hash || canonical_json(event))``
  links each event to its predecessor.
- ``audit_chain_head`` — one tip-checkpoint row per chain (last ``seq`` + tip
  ``hash``). It serialises appends (optimistic concurrency) and lets
  ``verify_chain`` detect tail/full-chain deletion, which leaves no gap in
  ``audit_event``.

Key integrity (see the W0 RFC §6):
- composite FK ``(org_id, project_id) -> project(org_id, id)`` (MATCH SIMPLE,
  so org-level NULL-project rows are exempt) so a row can't claim one org while
  pointing at another org's project;
- NULL-safe partial unique sequence indexes so no two events share a ``seq``
  within a chain (``ux_audit_seq_proj`` / ``ux_audit_seq_org``), and one head per
  chain (``ux_audit_head_proj`` / ``ux_audit_head_org``). Audit reads do NOT
  inherit the org-shared ``project_id IS NULL`` rule — each tenant has an
  independent chain.

SQLite gets this schema from Base.metadata.create_all (the models mirror this
DDL); Alembic runs only on Postgres.

Revision ID: 010_tenant_audit
Revises: 009_tenancy_identity
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "010_tenant_audit"
down_revision: str | None = "009_tenancy_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    empty_obj = sa.text("'{}'::jsonb") if is_pg else sa.text("'{}'")

    op.create_table(
        "audit_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("org.id"), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=empty_obj,
        ),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("prev_hash", sa.Text(), nullable=True),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # MATCH SIMPLE (default): NULL project_id (org-level) rows are exempt.
        sa.ForeignKeyConstraint(["org_id", "project_id"], ["project.org_id", "project.id"]),
    )
    # NULL-safe per-chain sequence uniqueness (one seq per (org, project) chain).
    op.create_index(
        "ux_audit_seq_proj",
        "audit_event",
        ["org_id", "project_id", "seq"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.create_index(
        "ux_audit_seq_org",
        "audit_event",
        ["org_id", "seq"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )

    op.create_table(
        "audit_chain_head",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("org.id"), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # MATCH SIMPLE (default): NULL project_id (org-level) rows are exempt.
        sa.ForeignKeyConstraint(["org_id", "project_id"], ["project.org_id", "project.id"]),
    )
    # One head per (org, project) chain.
    op.create_index(
        "ux_audit_head_proj",
        "audit_chain_head",
        ["org_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.create_index(
        "ux_audit_head_org",
        "audit_chain_head",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_audit_head_org", table_name="audit_chain_head")
    op.drop_index("ux_audit_head_proj", table_name="audit_chain_head")
    op.drop_table("audit_chain_head")
    op.drop_index("ux_audit_seq_org", table_name="audit_event")
    op.drop_index("ux_audit_seq_proj", table_name="audit_event")
    op.drop_table("audit_event")
