# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""task due index

Revision ID: 029_task_due_index
Revises: 028_task_coordinator
"""

from __future__ import annotations

from alembic import op

revision = "029_task_due_index"
down_revision = "028_task_coordinator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_tasks_scope_due", "tasks", ["project_scope_key", "next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_scope_due", table_name="tasks")
