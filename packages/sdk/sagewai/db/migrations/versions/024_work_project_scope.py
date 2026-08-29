# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""deterministic project-scope persistence identities

Revision ID: 024_work_project_scope
Revises: 023_knowledge_source_refs
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "024_work_project_scope"
down_revision = "023_knowledge_source_refs"
branch_labels = None
depends_on = None

_SCOPE_SQL = "CASE WHEN project_id IS NULL THEN 'g:' ELSE 'p:' || project_id END"


def _json_type() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _create_work_items(name: str, *, scoped: bool) -> None:
    columns: list[sa.SchemaItem] = []
    if scoped:
        columns.append(sa.Column("project_scope_key", sa.Text(), nullable=False))
    columns.extend(
        [
            sa.Column("work_id", sa.Text(), nullable=False),
            sa.Column("project_id", sa.Text(), nullable=True),
            sa.Column("source_ref", sa.Text(), nullable=True),
            sa.Column("profile", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("contract_version", sa.Integer(), nullable=True),
            sa.Column("active_run_id", sa.Text(), nullable=True),
            sa.Column("pending_gate", sa.Text(), nullable=True),
            sa.Column("profile_context", _json_type(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        ]
    )
    key = ["project_scope_key", "work_id"] if scoped else ["work_id"]
    op.create_table(name, *columns, sa.PrimaryKeyConstraint(*key, name="pk_work_items"))


def _create_work_events(name: str, *, scoped: bool) -> None:
    columns: list[sa.SchemaItem] = []
    if scoped:
        columns.append(sa.Column("project_scope_key", sa.Text(), nullable=False))
    columns.extend(
        [
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("project_id", sa.Text(), nullable=True),
            sa.Column("work_id", sa.Text(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.Text(), nullable=False),
            sa.Column("actor_type", sa.Text(), nullable=False),
            sa.Column("actor_ref", sa.Text(), nullable=True),
            sa.Column("payload_json", _json_type(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        ]
    )
    primary_key = ["project_scope_key", "id"] if scoped else ["id"]
    sequence_key = (
        ["project_scope_key", "work_id", "sequence"]
        if scoped
        else ["work_id", "sequence"]
    )
    op.create_table(
        name,
        *columns,
        sa.PrimaryKeyConstraint(*primary_key, name="pk_work_events"),
        sa.UniqueConstraint(*sequence_key, name="uq_work_events_work_sequence"),
    )


def _create_knowledge_items(name: str, *, scoped: bool) -> None:
    columns: list[sa.SchemaItem] = []
    if scoped:
        columns.append(sa.Column("project_scope_key", sa.Text(), nullable=False))
    columns.extend(
        [
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("project_id", sa.Text(), nullable=scoped),
            sa.Column("work_id", sa.Text(), nullable=True),
            sa.Column("kind", sa.Text(), nullable=False),
            sa.Column("statement", sa.Text(), nullable=False),
            sa.Column("source_refs", _json_type(), nullable=False),
            sa.Column("artifact_refs", _json_type(), nullable=False),
            sa.Column("factness_score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("importance_score", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("created_by", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("supersedes", sa.Text(), nullable=True),
        ]
    )
    key = ["project_scope_key", "id"] if scoped else ["id"]
    op.create_table(
        name,
        *columns,
        sa.PrimaryKeyConstraint(*key, name="pk_knowledge_items"),
        sa.CheckConstraint(
            "factness_score IN (0, 100)",
            name="ck_knowledge_items_factness_score",
        ),
    )


def _create_source_refs(name: str, *, scoped: bool) -> None:
    columns: list[sa.SchemaItem] = []
    if scoped:
        columns.append(sa.Column("project_scope_key", sa.Text(), nullable=False))
    columns.extend(
        [
            sa.Column("knowledge_item_id", sa.Text(), nullable=False),
            sa.Column("project_id", sa.Text(), nullable=scoped),
            sa.Column("source_ref", sa.Text(), nullable=False),
        ]
    )
    key = (
        ["project_scope_key", "knowledge_item_id", "source_ref"]
        if scoped
        else ["knowledge_item_id", "source_ref"]
    )
    foreign_columns = (
        ["project_scope_key", "knowledge_item_id"]
        if scoped
        else ["knowledge_item_id"]
    )
    remote_columns = (
        ["knowledge_items.project_scope_key", "knowledge_items.id"]
        if scoped
        else ["knowledge_items.id"]
    )
    op.create_table(
        name,
        *columns,
        sa.PrimaryKeyConstraint(*key, name="pk_knowledge_source_refs"),
        sa.ForeignKeyConstraint(
            foreign_columns,
            remote_columns,
            name=("fk_knowledge_source_refs_scoped_item" if scoped else None),
            ondelete="CASCADE",
        ),
    )


def _sqlite_rebuild(scoped: bool) -> None:
    scope_column = "project_scope_key, " if scoped else ""
    scope_value = f"{_SCOPE_SQL}, " if scoped else ""

    op.execute("DROP TABLE IF EXISTS knowledge_items_fts")
    op.execute(
        "CREATE TABLE _024_source_refs AS SELECT knowledge_item_id, project_id, source_ref "
        "FROM knowledge_source_refs"
    )
    op.drop_table("knowledge_source_refs")

    _create_work_items("_024_work_items", scoped=scoped)
    op.execute(
        f"INSERT INTO _024_work_items ({scope_column}work_id, project_id, source_ref, profile, "
        "status, contract_version, active_run_id, pending_gate, profile_context, created_at, updated_at) "
        f"SELECT {scope_value}work_id, project_id, source_ref, profile, status, contract_version, "
        "active_run_id, pending_gate, profile_context, created_at, updated_at FROM work_items"
    )
    op.drop_table("work_items")
    op.rename_table("_024_work_items", "work_items")

    _create_work_events("_024_work_events", scoped=scoped)
    op.execute(
        f"INSERT INTO _024_work_events ({scope_column}id, project_id, work_id, sequence, "
        "event_type, actor_type, actor_ref, payload_json, created_at) "
        f"SELECT {scope_value}id, project_id, work_id, sequence, event_type, actor_type, "
        "actor_ref, payload_json, created_at FROM work_events"
    )
    op.drop_table("work_events")
    op.rename_table("_024_work_events", "work_events")

    _create_knowledge_items("_024_knowledge_items", scoped=scoped)
    op.execute(
        f"INSERT INTO _024_knowledge_items ({scope_column}id, project_id, work_id, kind, statement, "
        "source_refs, artifact_refs, factness_score, importance_score, created_by, created_at, supersedes) "
        f"SELECT {scope_value}id, project_id, work_id, kind, statement, source_refs, artifact_refs, "
        "factness_score, importance_score, created_by, created_at, supersedes FROM knowledge_items"
    )
    op.drop_table("knowledge_items")
    op.rename_table("_024_knowledge_items", "knowledge_items")

    _create_source_refs("knowledge_source_refs", scoped=scoped)
    op.execute(
        f"INSERT INTO knowledge_source_refs ({scope_column}knowledge_item_id, project_id, source_ref) "
        f"SELECT {scope_value}knowledge_item_id, project_id, source_ref FROM _024_source_refs"
    )
    op.drop_table("_024_source_refs")

    if scoped:
        op.create_index(
            "ix_work_items_project_scope",
            "work_items",
            ["project_scope_key"],
        )
        op.create_index(
            "ix_work_events_scope_work_sequence",
            "work_events",
            ["project_scope_key", "work_id", "sequence"],
        )
        op.create_index(
            "ix_knowledge_items_scope_work_created_at",
            "knowledge_items",
            ["project_scope_key", "work_id", "created_at"],
        )
        op.create_index(
            "ix_knowledge_source_refs_scope_ref_item",
            "knowledge_source_refs",
            ["project_scope_key", "source_ref", "knowledge_item_id"],
        )
        op.execute(
            "CREATE VIRTUAL TABLE knowledge_items_fts USING "
            "fts5(project_scope_key UNINDEXED, item_id UNINDEXED, statement)"
        )
        op.execute(
            "INSERT INTO knowledge_items_fts (project_scope_key, item_id, statement) "
            "SELECT project_scope_key, id, statement FROM knowledge_items"
        )
    else:
        op.create_index("ix_work_items_project_id", "work_items", ["project_id"])
        op.create_index(
            "ix_work_events_project_work_sequence",
            "work_events",
            ["project_id", "work_id", "sequence"],
        )
        op.create_index(
            "ix_knowledge_items_project_work_created_at",
            "knowledge_items",
            ["project_id", "work_id", "created_at"],
        )
        op.create_index(
            "ix_knowledge_source_refs_project_ref_item",
            "knowledge_source_refs",
            ["project_id", "source_ref", "knowledge_item_id"],
        )
        op.execute(
            "CREATE VIRTUAL TABLE knowledge_items_fts USING "
            "fts5(item_id UNINDEXED, statement)"
        )
        op.execute(
            "INSERT INTO knowledge_items_fts (item_id, statement) "
            "SELECT id, statement FROM knowledge_items"
        )


def _constraint_name(bind: sa.Connection, table: str, kind: str) -> str:
    inspector = sa.inspect(bind)
    constraint = (
        inspector.get_pk_constraint(table)
        if kind == "primary"
        else next(iter(inspector.get_foreign_keys(table)), {})
    )
    name = constraint.get("name")
    if not name:
        raise RuntimeError(f"Cannot migrate unnamed {kind} constraint on {table}")
    return str(name)


def _postgres_upgrade() -> None:
    bind = op.get_bind()
    for table in ("work_items", "work_events", "knowledge_items", "knowledge_source_refs"):
        op.add_column(table, sa.Column("project_scope_key", sa.Text(), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET project_scope_key = {_SCOPE_SQL}"))
        op.alter_column(table, "project_scope_key", existing_type=sa.Text(), nullable=False)

    op.drop_constraint(
        _constraint_name(bind, "knowledge_source_refs", "foreign"),
        "knowledge_source_refs",
        type_="foreignkey",
    )
    for table in ("work_items", "work_events", "knowledge_items", "knowledge_source_refs"):
        op.drop_constraint(_constraint_name(bind, table, "primary"), table, type_="primary")
    op.drop_constraint("uq_work_events_work_sequence", "work_events", type_="unique")
    op.alter_column("knowledge_items", "project_id", existing_type=sa.Text(), nullable=True)
    op.alter_column("knowledge_source_refs", "project_id", existing_type=sa.Text(), nullable=True)

    op.create_primary_key("pk_work_items", "work_items", ["project_scope_key", "work_id"])
    op.create_primary_key("pk_work_events", "work_events", ["project_scope_key", "id"])
    op.create_unique_constraint(
        "uq_work_events_work_sequence",
        "work_events",
        ["project_scope_key", "work_id", "sequence"],
    )
    op.create_primary_key("pk_knowledge_items", "knowledge_items", ["project_scope_key", "id"])
    op.create_primary_key(
        "pk_knowledge_source_refs",
        "knowledge_source_refs",
        ["project_scope_key", "knowledge_item_id", "source_ref"],
    )
    op.create_foreign_key(
        "fk_knowledge_source_refs_scoped_item",
        "knowledge_source_refs",
        "knowledge_items",
        ["project_scope_key", "knowledge_item_id"],
        ["project_scope_key", "id"],
        ondelete="CASCADE",
    )

    for table, old_name, new_name, columns in (
        ("work_items", "ix_work_items_project_id", "ix_work_items_project_scope", ["project_scope_key"]),
        (
            "work_events",
            "ix_work_events_project_work_sequence",
            "ix_work_events_scope_work_sequence",
            ["project_scope_key", "work_id", "sequence"],
        ),
        (
            "knowledge_items",
            "ix_knowledge_items_project_work_created_at",
            "ix_knowledge_items_scope_work_created_at",
            ["project_scope_key", "work_id", "created_at"],
        ),
        (
            "knowledge_source_refs",
            "ix_knowledge_source_refs_project_ref_item",
            "ix_knowledge_source_refs_scope_ref_item",
            ["project_scope_key", "source_ref", "knowledge_item_id"],
        ),
    ):
        op.drop_index(old_name, table_name=table)
        op.create_index(new_name, table, columns)


def _has_row(bind: sa.Connection, statement: str) -> bool:
    return bind.execute(sa.text(statement)).first() is not None


def _assert_legacy_identity_safe(bind: sa.Connection) -> None:
    checks = (
        ("duplicate work_items.work_id", "SELECT 1 FROM work_items GROUP BY work_id HAVING COUNT(*) > 1 LIMIT 1"),
        ("duplicate work_events.id", "SELECT 1 FROM work_events GROUP BY id HAVING COUNT(*) > 1 LIMIT 1"),
        (
            "duplicate work event sequence",
            "SELECT 1 FROM work_events GROUP BY work_id, sequence HAVING COUNT(*) > 1 LIMIT 1",
        ),
        ("duplicate knowledge_items.id", "SELECT 1 FROM knowledge_items GROUP BY id HAVING COUNT(*) > 1 LIMIT 1"),
        (
            "duplicate knowledge source ref",
            "SELECT 1 FROM knowledge_source_refs GROUP BY knowledge_item_id, source_ref "
            "HAVING COUNT(*) > 1 LIMIT 1",
        ),
        ("global knowledge item", "SELECT 1 FROM knowledge_items WHERE project_id IS NULL LIMIT 1"),
        (
            "global knowledge source ref",
            "SELECT 1 FROM knowledge_source_refs WHERE project_id IS NULL LIMIT 1",
        ),
    )
    failures = [label for label, query in checks if _has_row(bind, query)]
    if failures:
        raise RuntimeError(
            "Cannot downgrade 024_work_project_scope: data cannot fit legacy unscoped identities "
            f"({', '.join(failures)})"
        )


def _postgres_downgrade() -> None:
    op.drop_constraint(
        "fk_knowledge_source_refs_scoped_item",
        "knowledge_source_refs",
        type_="foreignkey",
    )
    for table, name in (
        ("work_items", "pk_work_items"),
        ("work_events", "pk_work_events"),
        ("knowledge_items", "pk_knowledge_items"),
        ("knowledge_source_refs", "pk_knowledge_source_refs"),
    ):
        op.drop_constraint(name, table, type_="primary")
    op.drop_constraint("uq_work_events_work_sequence", "work_events", type_="unique")

    op.create_primary_key(None, "work_items", ["work_id"])
    op.create_primary_key(None, "work_events", ["id"])
    op.create_unique_constraint(
        "uq_work_events_work_sequence", "work_events", ["work_id", "sequence"]
    )
    op.create_primary_key(None, "knowledge_items", ["id"])
    op.create_primary_key(
        "pk_knowledge_source_refs",
        "knowledge_source_refs",
        ["knowledge_item_id", "source_ref"],
    )
    op.create_foreign_key(
        None,
        "knowledge_source_refs",
        "knowledge_items",
        ["knowledge_item_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("knowledge_items", "project_id", existing_type=sa.Text(), nullable=False)
    op.alter_column("knowledge_source_refs", "project_id", existing_type=sa.Text(), nullable=False)

    for table, scoped_name, legacy_name, columns in (
        ("work_items", "ix_work_items_project_scope", "ix_work_items_project_id", ["project_id"]),
        (
            "work_events",
            "ix_work_events_scope_work_sequence",
            "ix_work_events_project_work_sequence",
            ["project_id", "work_id", "sequence"],
        ),
        (
            "knowledge_items",
            "ix_knowledge_items_scope_work_created_at",
            "ix_knowledge_items_project_work_created_at",
            ["project_id", "work_id", "created_at"],
        ),
        (
            "knowledge_source_refs",
            "ix_knowledge_source_refs_scope_ref_item",
            "ix_knowledge_source_refs_project_ref_item",
            ["project_id", "source_ref", "knowledge_item_id"],
        ),
    ):
        op.drop_index(scoped_name, table_name=table)
        op.create_index(legacy_name, table, columns)

    for table in ("knowledge_source_refs", "knowledge_items", "work_events", "work_items"):
        op.drop_column(table, "project_scope_key")


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        _sqlite_rebuild(scoped=True)
    else:
        _postgres_upgrade()


def downgrade() -> None:
    bind = op.get_bind()
    _assert_legacy_identity_safe(bind)
    if bind.dialect.name == "sqlite":
        _sqlite_rebuild(scoped=False)
    else:
        _postgres_downgrade()
