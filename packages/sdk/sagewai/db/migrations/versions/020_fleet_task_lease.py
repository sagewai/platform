# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""fleet B2: per-task lease + attempts for dead-worker requeue

Revision ID: 020_fleet_task_lease
Revises: 019_fleet_tasks
"""

import sqlalchemy as sa
from alembic import op

revision = "020_fleet_task_lease"
down_revision = "019_fleet_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fleet_tasks", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("fleet_tasks", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_fleet_tasks_lease", "fleet_tasks", ["status", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_fleet_tasks_lease", table_name="fleet_tasks")
    op.drop_column("fleet_tasks", "attempts")
    op.drop_column("fleet_tasks", "lease_expires_at")
