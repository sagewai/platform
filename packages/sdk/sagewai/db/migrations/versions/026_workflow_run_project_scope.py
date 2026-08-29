# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""make durable workflow-run identity explicitly project scoped

Revision ID: 026_workflow_run_project_scope
Revises: 025_assumption_project_scope
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "026_workflow_run_project_scope"
down_revision = "025_assumption_project_scope"
branch_labels = None
depends_on = None

_SCOPE_SQL = "CASE WHEN project_id IS NULL THEN 'g:' ELSE 'p:' || project_id END"
_SCOPED_ID_SQL = (
    "CAST(length("
    + _SCOPE_SQL
    + ") AS TEXT) || ':' || "
    + _SCOPE_SQL
    + " || CAST(length(workflow_name) AS TEXT) || ':' || workflow_name || run_id"
)
_SCOPED_IDEMPOTENCY_SQL = (
    "CAST(length(" + _SCOPE_SQL + ") AS TEXT) || ':' || " + _SCOPE_SQL + " || idempotency_key"
)
_LEGACY_ID_SQL = "workflow_name || ':' || run_id"
_LEGACY_IDEMPOTENCY_SQL = (
    "substr(idempotency_key, "
    "length(CAST(length(" + _SCOPE_SQL + ") AS TEXT)) "
    "+ length(" + _SCOPE_SQL + ") + 2)"
)
_IDEMPOTENCY_PREFIX_SQL = "CAST(length(" + _SCOPE_SQL + ") AS TEXT) || ':' || " + _SCOPE_SQL


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in ("workflow_runs", "workflow_events"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "project_id",
                existing_type=sa.Text(),
                nullable=True,
                server_default=None,
            )

    if bind.dialect.name == "sqlite":
        project_expression = "json_extract(data, '$.project_id')"
        has_project = "json_type(data, '$.project_id') IS NOT NULL"
    else:
        project_expression = "data ->> 'project_id'"
        has_project = "data ? 'project_id'"

    op.execute(
        sa.text(
            "UPDATE workflow_runs " f"SET project_id = {project_expression} WHERE {has_project}"
        )
    )

    ambiguous_event = bind.execute(
        sa.text(
            "SELECT 1 FROM workflow_events e "
            "JOIN workflow_runs r ON r.run_id = e.run_id "
            "GROUP BY e.run_id "
            "HAVING COUNT(DISTINCT CASE WHEN r.project_id IS NULL THEN 'g:' "
            "ELSE 'p:' || r.project_id END) > 1 LIMIT 1"
        )
    ).first()
    if ambiguous_event is not None:
        raise RuntimeError("cannot migrate workflow events with ambiguous run project scope")

    op.execute(
        sa.text(
            "UPDATE workflow_events SET project_id = ("
            "SELECT r.project_id FROM workflow_runs r "
            "WHERE r.run_id = workflow_events.run_id LIMIT 1"
            ") WHERE EXISTS ("
            "SELECT 1 FROM workflow_runs r "
            "WHERE r.run_id = workflow_events.run_id)"
        )
    )
    op.execute(
        sa.text(
            f"UPDATE workflow_runs SET id = {_SCOPED_ID_SQL}, "
            "idempotency_key = CASE WHEN idempotency_key IS NULL THEN NULL "
            f"ELSE {_SCOPED_IDEMPOTENCY_SQL} END"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    global_runs = bind.execute(
        sa.text("SELECT COUNT(*) FROM workflow_runs WHERE project_id IS NULL")
    ).scalar_one()
    global_events = bind.execute(
        sa.text("SELECT COUNT(*) FROM workflow_events WHERE project_id IS NULL")
    ).scalar_one()
    if global_runs or global_events:
        raise RuntimeError("cannot downgrade workflow scope while global runs or events exist")

    collision = bind.execute(
        sa.text(
            "SELECT 1 FROM workflow_runs GROUP BY workflow_name, run_id "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if collision is not None:
        raise RuntimeError("cannot downgrade workflow run scope while cross-project IDs collide")

    malformed_idempotency = bind.execute(
        sa.text(
            "SELECT 1 FROM workflow_runs WHERE idempotency_key IS NOT NULL "
            f"AND substr(idempotency_key, 1, length({_IDEMPOTENCY_PREFIX_SQL})) "
            f"!= {_IDEMPOTENCY_PREFIX_SQL} LIMIT 1"
        )
    ).first()
    if malformed_idempotency is not None:
        raise RuntimeError("cannot downgrade malformed scoped workflow idempotency keys")

    idempotency_collision = bind.execute(
        sa.text(
            f"SELECT 1 FROM workflow_runs WHERE idempotency_key IS NOT NULL "
            f"GROUP BY {_LEGACY_IDEMPOTENCY_SQL} HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if idempotency_collision is not None:
        raise RuntimeError("cannot downgrade workflow scope while idempotency keys collide")

    op.execute(
        sa.text(
            f"UPDATE workflow_runs SET id = {_LEGACY_ID_SQL}, "
            "idempotency_key = CASE WHEN idempotency_key IS NULL THEN NULL "
            f"ELSE {_LEGACY_IDEMPOTENCY_SQL} END"
        )
    )
    for table_name in ("workflow_events", "workflow_runs"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "project_id",
                existing_type=sa.Text(),
                nullable=False,
                server_default="default",
            )
