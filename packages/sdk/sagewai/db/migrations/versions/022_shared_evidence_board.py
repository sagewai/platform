# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""minimal shared Evidence Board

Revision ID: 022_shared_evidence_board
Revises: 021_work_domain
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "022_shared_evidence_board"
down_revision = "021_work_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_items",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("work_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(), nullable=False),
        sa.Column("artifact_refs", postgresql.JSONB(), nullable=False),
        sa.Column(
            "factness_score",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "importance_score",
            sa.Integer(),
            nullable=False,
            server_default="50",
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("supersedes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "factness_score IN (0, 100)",
            name="ck_knowledge_items_factness_score",
        ),
    )
    op.create_index(
        "ix_knowledge_items_project_work_created_at",
        "knowledge_items",
        ["project_id", "work_id", "created_at"],
    )
    op.execute(
        "CREATE INDEX ix_knowledge_items_statement_fts "
        "ON knowledge_items USING GIN (to_tsvector('simple', statement))"
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_items_statement_fts", table_name="knowledge_items")
    op.drop_index(
        "ix_knowledge_items_project_work_created_at",
        table_name="knowledge_items",
    )
    op.drop_table("knowledge_items")
