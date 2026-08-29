# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SQLite startup upgrade for project-scoped Fleet task identities."""

from __future__ import annotations

import logging

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.schema import CreateTable

from sagewai.db.models import FleetTaskModel

logger = logging.getLogger(__name__)

_SCOPE_SQL = "CASE WHEN project_id IS NULL THEN 'g:' ELSE 'p:' || project_id END"
_EXPECTED_PRIMARY_KEY = ["org_id", "project_scope_key", "run_id"]


def upgrade_sqlite_fleet_task_scope(sync_conn) -> None:
    """Rebuild a legacy queue without losing pending or terminal task rows."""
    inspector = inspect(sync_conn)
    if "fleet_tasks" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("fleet_tasks")}
    primary_key = inspector.get_pk_constraint("fleet_tasks")["constrained_columns"]
    if "project_scope_key" in columns and primary_key == _EXPECTED_PRIMARY_KEY:
        return

    temporary_name = "_sagewai_027_fleet_tasks"
    if temporary_name in inspector.get_table_names():
        raise RuntimeError("cannot upgrade fleet_tasks: temporary migration table already exists")

    metadata = MetaData()
    replacement = FleetTaskModel.__table__.to_metadata(metadata, name=temporary_name)
    sync_conn.execute(CreateTable(replacement))

    quote = sync_conn.dialect.identifier_preparer.quote
    target_columns: list[str] = []
    source_expressions: list[str] = []
    for column in replacement.columns:
        if column.name == "project_scope_key":
            target_columns.append(quote(column.name))
            source_expressions.append(_SCOPE_SQL)
        elif column.name in columns:
            target_columns.append(quote(column.name))
            source_expressions.append(quote(column.name))

    sync_conn.execute(
        text(
            f"INSERT INTO {quote(temporary_name)} ({', '.join(target_columns)}) "
            f"SELECT {', '.join(source_expressions)} FROM {quote('fleet_tasks')}"
        )
    )
    sync_conn.execute(text(f"DROP TABLE {quote('fleet_tasks')}"))
    sync_conn.execute(
        text(f"ALTER TABLE {quote(temporary_name)} RENAME TO {quote('fleet_tasks')}")
    )
    for index in FleetTaskModel.__table__.indexes:
        index.create(bind=sync_conn, checkfirst=True)
    logger.info("sqlite home upgrade: scoped fleet_tasks durable identity")
