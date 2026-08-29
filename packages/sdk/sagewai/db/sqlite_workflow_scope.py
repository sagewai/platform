# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SQLite startup upgrade for explicitly scoped workflow durability rows."""

from __future__ import annotations

import logging

from sqlalchemy import MetaData, Table, inspect, text
from sqlalchemy.schema import CreateTable

logger = logging.getLogger(__name__)

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


def _make_project_nullable(sync_conn, table_name: str) -> None:
    """Rebuild one SQLite table while changing only ``project_id`` metadata."""
    temporary_name = f"_sagewai_026_{table_name}"
    if temporary_name in inspect(sync_conn).get_table_names():
        raise RuntimeError(f"cannot upgrade {table_name}: temporary migration table already exists")

    schema_row = sync_conn.execute(
        text("SELECT sql FROM sqlite_master " "WHERE type = 'table' AND name = :table_name"),
        {"table_name": table_name},
    ).one()
    original_table_sql = schema_row[0] or ""
    schema_objects = sync_conn.execute(
        text(
            "SELECT type, sql FROM sqlite_master "
            "WHERE tbl_name = :table_name "
            "AND type IN ('index', 'trigger') AND sql IS NOT NULL "
            "ORDER BY type, name"
        ),
        {"table_name": table_name},
    ).all()

    reflected_metadata = MetaData()
    reflected = Table(table_name, reflected_metadata, autoload_with=sync_conn)
    replacement_metadata = MetaData()
    replacement = reflected.to_metadata(replacement_metadata, name=temporary_name)
    replacement.c.project_id.nullable = True
    replacement.c.project_id.server_default = None
    if "AUTOINCREMENT" in original_table_sql.upper():
        replacement.dialect_options["sqlite"]["autoincrement"] = True

    sync_conn.execute(CreateTable(replacement))
    quote = sync_conn.dialect.identifier_preparer.quote
    column_list = ", ".join(quote(column.name) for column in reflected.columns)
    sync_conn.execute(
        text(
            f"INSERT INTO {quote(temporary_name)} ({column_list}) "
            f"SELECT {column_list} FROM {quote(table_name)}"
        )
    )
    sync_conn.execute(text(f"DROP TABLE {quote(table_name)}"))
    sync_conn.execute(text(f"ALTER TABLE {quote(temporary_name)} RENAME TO {quote(table_name)}"))
    for _object_type, create_sql in schema_objects:
        sync_conn.execute(text(create_sql))


def upgrade_sqlite_workflow_scope(sync_conn) -> None:
    """Apply migration 026 semantics to a pre-026 local SQLite home.

    SQLite homes are bootstrapped with ``create_all`` rather than Alembic. The
    old ``NOT NULL DEFAULT 'default'`` project column is the durable migration
    marker; after the transactional table rebuild this function is a no-op.
    """
    table_names = set(inspect(sync_conn).get_table_names())
    if "workflow_runs" not in table_names:
        return

    def needs_upgrade(table_name: str) -> bool:
        if table_name not in table_names:
            return False
        project_column = next(
            (
                column
                for column in inspect(sync_conn).get_columns(table_name)
                if column["name"] == "project_id"
            ),
            None,
        )
        return project_column is not None and (
            not project_column["nullable"] or project_column["default"] is not None
        )

    runs_need_upgrade = needs_upgrade("workflow_runs")
    events_need_upgrade = needs_upgrade("workflow_events")
    if not runs_need_upgrade and not events_need_upgrade:
        return

    if runs_need_upgrade:
        sync_conn.execute(
            text(
                "UPDATE workflow_runs "
                "SET project_id = json_extract(data, '$.project_id') "
                "WHERE json_type(data, '$.project_id') IS NOT NULL"
            )
        )

    if events_need_upgrade:
        ambiguous_event = sync_conn.execute(
            text(
                "SELECT 1 FROM workflow_events e "
                "JOIN workflow_runs r ON r.run_id = e.run_id "
                "GROUP BY e.run_id "
                "HAVING COUNT(DISTINCT CASE WHEN r.project_id IS NULL THEN 'g:' "
                "ELSE 'p:' || r.project_id END) > 1 LIMIT 1"
            )
        ).first()
        if ambiguous_event is not None:
            raise RuntimeError("cannot migrate workflow events with ambiguous run project scope")
        sync_conn.execute(
            text(
                "UPDATE workflow_events SET project_id = ("
                "SELECT r.project_id FROM workflow_runs r "
                "WHERE r.run_id = workflow_events.run_id LIMIT 1"
                ") WHERE EXISTS ("
                "SELECT 1 FROM workflow_runs r "
                "WHERE r.run_id = workflow_events.run_id)"
            )
        )

    if runs_need_upgrade:
        run_columns = {column["name"] for column in inspect(sync_conn).get_columns("workflow_runs")}
        idempotency_assignment = ""
        if "idempotency_key" in run_columns:
            idempotency_assignment = (
                ", idempotency_key = CASE WHEN idempotency_key IS NULL THEN NULL "
                f"ELSE {_SCOPED_IDEMPOTENCY_SQL} END"
            )
        sync_conn.execute(
            text(f"UPDATE workflow_runs SET id = {_SCOPED_ID_SQL}" f"{idempotency_assignment}")
        )

    for table_name, table_needs_upgrade in (
        ("workflow_runs", runs_need_upgrade),
        ("workflow_events", events_need_upgrade),
    ):
        if not table_needs_upgrade:
            continue
        _make_project_nullable(sync_conn, table_name)
        logger.info("sqlite home upgrade: scoped %s project identity", table_name)
