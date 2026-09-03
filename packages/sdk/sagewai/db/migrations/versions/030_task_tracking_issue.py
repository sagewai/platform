# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""task tracking issue

Revision ID: 030_task_tracking_issue
Revises: 029_task_due_index
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "030_task_tracking_issue"
down_revision = "029_task_due_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("tracking_issue_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "tracking_issue_url")
