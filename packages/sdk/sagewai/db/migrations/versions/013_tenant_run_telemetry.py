# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""nullable project_id on run telemetry tables

Makes ``agent_runs.project_id`` and ``prompt_logs.project_id`` nullable so a
NULL value means org-shared/global (the §3 tenant data-scope contract), while
the ``'default'`` server_default keeps single-org and engine writers stamping a
concrete project. Existing rows are untouched.

Revision ID: 013_tenant_run_telemetry
Revises: 012_tenant_agent
"""

import sqlalchemy as sa
from alembic import op

revision = "013_tenant_run_telemetry"
down_revision = "012_tenant_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "agent_runs",
        "project_id",
        existing_type=sa.Text(),
        nullable=True,
        existing_server_default=sa.text("'default'"),
    )
    op.alter_column(
        "prompt_logs",
        "project_id",
        existing_type=sa.Text(),
        nullable=True,
        existing_server_default=sa.text("'default'"),
    )


def downgrade() -> None:
    op.alter_column(
        "prompt_logs",
        "project_id",
        existing_type=sa.Text(),
        nullable=False,
        existing_server_default=sa.text("'default'"),
    )
    op.alter_column(
        "agent_runs",
        "project_id",
        existing_type=sa.Text(),
        nullable=False,
        existing_server_default=sa.text("'default'"),
    )
