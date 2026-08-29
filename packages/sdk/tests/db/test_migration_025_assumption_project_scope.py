# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 025: project scope in durable assumption payloads."""

from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql, sqlite

from sagewai.db.models import WorkEventModel


def _module():
    return importlib.import_module(
        "sagewai.db.migrations.versions.025_assumption_project_scope"
    )


def test_migration_025_revision_chain() -> None:
    mod = _module()

    assert mod.revision == "025_assumption_project_scope"
    assert mod.down_revision == "024_work_project_scope"
    assert callable(mod.upgrade) and callable(mod.downgrade)


@pytest.mark.parametrize(
    ("dialect", "add_fragment", "remove_fragment"),
    [
        (sqlite.dialect(), "json_set(payload_json", "json_remove(payload_json"),
        (postgresql.dialect(), "jsonb_set(payload_json", "payload_json - 'project_id'"),
    ],
)
def test_payload_mutation_uses_dialect_json_operators(
    monkeypatch, dialect, add_fragment: str, remove_fragment: str
) -> None:
    mod = _module()
    executed: list[str] = []

    class RecordingOp:
        def get_bind(self):
            return type("Bind", (), {"dialect": dialect})()

        def execute(self, statement) -> None:
            executed.append(str(statement))

    monkeypatch.setattr(mod, "op", RecordingOp())

    mod.upgrade()
    mod.downgrade()

    assert add_fragment in executed[0]
    assert remove_fragment in executed[1]
    assert all("event_type = 'ASSUMPTION_RECORDED'" in statement for statement in executed)
    if dialect.name == "postgresql":
        assert "CASE WHEN project_id IS NULL THEN 'null'::jsonb" in executed[0]


def test_sqlite_upgrade_and_downgrade_preserve_canonical_scope() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        WorkEventModel.__table__.create(connection)
        connection.exec_driver_sql(
            "INSERT INTO work_events "
            "(project_scope_key, id, project_id, work_id, sequence, event_type, actor_type, "
            "payload_json, created_at) VALUES "
            "('g:', 'global', NULL, 'work', 1, 'ASSUMPTION_RECORDED', 'system', "
            "json_object('id', 'global'), '2026-01-01'), "
            "('p:alpha', 'project', 'alpha', 'work', 1, 'ASSUMPTION_RECORDED', 'system', "
            "json_object('id', 'project', 'project_id', 'wrong'), '2026-01-01'), "
            "('p:alpha', 'other', 'alpha', 'work', 2, 'WORK_CREATED', 'system', "
            "json_object('id', 'other'), '2026-01-01')"
        )
        mod = _module()
        original_op = mod.op
        mod.op = Operations(MigrationContext.configure(connection))
        try:
            mod.upgrade()
            rows = connection.exec_driver_sql(
                "SELECT id, json_extract(payload_json, '$.project_id'), "
                "json_type(payload_json, '$.project_id') FROM work_events ORDER BY id"
            ).all()
            assert rows == [
                ("global", None, "null"),
                ("other", None, None),
                ("project", "alpha", "text"),
            ]

            mod.downgrade()
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM work_events WHERE event_type = 'ASSUMPTION_RECORDED' "
                "AND json_type(payload_json, '$.project_id') IS NULL"
            ).scalar_one() == 2
        finally:
            mod.op = original_op
