# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""backfill project scope into durable assumption event payloads

Revision ID: 025_assumption_project_scope
Revises: 024_work_project_scope
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "025_assumption_project_scope"
down_revision = "024_work_project_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        expression = "json_set(payload_json, '$.project_id', project_id)"
    else:
        expression = (
            "jsonb_set(payload_json, '{project_id}', "
            "CASE WHEN project_id IS NULL THEN 'null'::jsonb "
            "ELSE to_jsonb(project_id) END, true)"
        )
    op.execute(
        sa.text(
            f"UPDATE work_events SET payload_json = {expression} "
            "WHERE event_type = 'ASSUMPTION_RECORDED'"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    expression = (
        "json_remove(payload_json, '$.project_id')"
        if bind.dialect.name == "sqlite"
        else "payload_json - 'project_id'"
    )
    op.execute(
        sa.text(
            f"UPDATE work_events SET payload_json = {expression} "
            "WHERE event_type = 'ASSUMPTION_RECORDED'"
        )
    )
