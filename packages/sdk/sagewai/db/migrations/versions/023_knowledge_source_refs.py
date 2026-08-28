# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""indexed KnowledgeItem source-reference navigation

Revision ID: 023_knowledge_source_refs
Revises: 022_shared_evidence_board
"""

import sqlalchemy as sa
from alembic import op

revision = "023_knowledge_source_refs"
down_revision = "022_shared_evidence_board"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_source_refs",
        sa.Column("knowledge_item_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_item_id"],
            ["knowledge_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "knowledge_item_id",
            "source_ref",
            name="pk_knowledge_source_refs",
        ),
    )
    op.create_index(
        "ix_knowledge_source_refs_project_ref_item",
        "knowledge_source_refs",
        ["project_id", "source_ref", "knowledge_item_id"],
    )
    op.execute(
        "INSERT INTO knowledge_source_refs "
        "(knowledge_item_id, project_id, source_ref) "
        "SELECT DISTINCT knowledge.id, knowledge.project_id, source.value "
        "FROM knowledge_items AS knowledge "
        "CROSS JOIN LATERAL "
        "jsonb_array_elements_text(knowledge.source_refs) AS source(value)"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_source_refs_project_ref_item",
        table_name="knowledge_source_refs",
    )
    op.drop_table("knowledge_source_refs")
