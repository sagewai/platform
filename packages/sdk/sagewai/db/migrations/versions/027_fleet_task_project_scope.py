# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""make durable Fleet task identity explicitly project scoped

Revision ID: 027_fleet_task_project_scope
Revises: 026_workflow_run_project_scope
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "027_fleet_task_project_scope"
down_revision = "026_workflow_run_project_scope"
branch_labels = None
depends_on = None

_SCOPE_SQL = "CASE WHEN project_id IS NULL THEN 'g:' ELSE 'p:' || project_id END"
_COLUMNS = (
    "run_id", "org_id", "project_id", "pool", "model", "labels", "payload",
    "status", "worker_id", "claimed_at", "output", "error", "reported_at",
    "created_at", "lease_expires_at", "attempts",
)


def _json_type() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _create_table(name: str, *, scoped: bool) -> None:
    columns: list[sa.SchemaItem] = [
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
    ]
    if scoped:
        columns.append(sa.Column("project_scope_key", sa.Text(), nullable=False))
    columns.extend([
        sa.Column("pool", sa.Text(), nullable=False, server_default="default"),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("labels", _json_type(), nullable=False),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    ])
    key = ["org_id", "project_scope_key", "run_id"] if scoped else ["run_id"]
    op.create_table(
        name,
        *columns,
        sa.PrimaryKeyConstraint(*key, name="pk_fleet_tasks"),
        sa.CheckConstraint(
            "status IN ('pending','claimed','completed','failed')",
            name="ck_fleet_tasks_status",
        ),
    )


def _create_indexes() -> None:
    op.create_index(
        "ix_fleet_tasks_claim", "fleet_tasks",
        ["status", "org_id", "project_id", "pool", "created_at"],
    )
    op.create_index(
        "ix_fleet_tasks_scope", "fleet_tasks", ["org_id", "project_id", "created_at"],
    )
    op.create_index(
        "ix_fleet_tasks_lease", "fleet_tasks", ["status", "lease_expires_at"],
    )


def _sqlite_rebuild(*, scoped: bool) -> None:
    _create_table("_027_fleet_tasks", scoped=scoped)
    target = (("project_scope_key",) + _COLUMNS) if scoped else _COLUMNS
    source = ((_SCOPE_SQL,) + _COLUMNS) if scoped else _COLUMNS
    op.execute(sa.text(
        f"INSERT INTO _027_fleet_tasks ({', '.join(target)}) "
        f"SELECT {', '.join(source)} FROM fleet_tasks"
    ))
    op.drop_table("fleet_tasks")
    op.rename_table("_027_fleet_tasks", "fleet_tasks")
    _create_indexes()


def _primary_key_name(bind: sa.Connection) -> str:
    name = sa.inspect(bind).get_pk_constraint("fleet_tasks").get("name")
    if not name:
        raise RuntimeError("cannot migrate unnamed fleet_tasks primary key")
    return str(name)


def _assert_legacy_identity_safe(bind: sa.Connection) -> None:
    collision = bind.execute(sa.text(
        "SELECT 1 FROM fleet_tasks GROUP BY run_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if collision is not None:
        raise RuntimeError(
            "cannot downgrade Fleet task scope while cross-scope run IDs collide"
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _sqlite_rebuild(scoped=True)
        return
    op.add_column("fleet_tasks", sa.Column("project_scope_key", sa.Text(), nullable=True))
    op.execute(sa.text(f"UPDATE fleet_tasks SET project_scope_key = {_SCOPE_SQL}"))
    op.alter_column(
        "fleet_tasks", "project_scope_key", existing_type=sa.Text(), nullable=False
    )
    op.drop_constraint(_primary_key_name(bind), "fleet_tasks", type_="primary")
    op.create_primary_key(
        "pk_fleet_tasks", "fleet_tasks", ["org_id", "project_scope_key", "run_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    _assert_legacy_identity_safe(bind)
    if bind.dialect.name == "sqlite":
        _sqlite_rebuild(scoped=False)
        return
    op.drop_constraint("pk_fleet_tasks", "fleet_tasks", type_="primary")
    op.create_primary_key(None, "fleet_tasks", ["run_id"])
    op.drop_column("fleet_tasks", "project_scope_key")
