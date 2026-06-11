# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""distributed fixed-window rate_limits counter (multi-tenant cross-process throttle)

Revision ID: 016_rate_limits
Revises: 015_admin_resources
"""

import sqlalchemy as sa
from alembic import op

revision = "016_rate_limits"
down_revision = "015_admin_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limits",
        sa.Column("bucket_key", sa.Text(), nullable=False),
        sa.Column("window_start", sa.BigInteger(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("bucket_key", "window_start"),
    )
    op.create_index("ix_rate_limits_window_start", "rate_limits", ["window_start"])


def downgrade() -> None:
    op.drop_table("rate_limits")
