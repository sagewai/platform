"""fleet B0: add name/secret_hash/approved_at/approved_by to workers; project_id nullable

Revision ID: 018_fleet_schema_correction
Revises: 017_api_tokens
"""

import sqlalchemy as sa
from alembic import op

revision = "018_fleet_schema_correction"
down_revision = "017_api_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workers", sa.Column("name", sa.Text(), nullable=True))
    op.add_column("workers", sa.Column("secret_hash", sa.Text(), nullable=True))
    op.add_column("workers", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workers", sa.Column("approved_by", sa.Text(), nullable=True))
    # project_id: NULL = org-global per the repo invariant (was NOT NULL DEFAULT 'default').
    # existing_type is required so Alembic emits a correct ALTER on all backends.
    op.alter_column(
        "workers", "project_id",
        existing_type=sa.Text(), nullable=True, server_default=None,
    )


def downgrade() -> None:
    # Backfill so the NOT NULL restore can't fail on org-global rows.
    op.execute("UPDATE workers SET project_id = 'default' WHERE project_id IS NULL")
    op.alter_column(
        "workers", "project_id",
        existing_type=sa.Text(), nullable=False, server_default="default",
    )
    op.drop_column("workers", "approved_by")
    op.drop_column("workers", "approved_at")
    op.drop_column("workers", "secret_hash")
    op.drop_column("workers", "name")
