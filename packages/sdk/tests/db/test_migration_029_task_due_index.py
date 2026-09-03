# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 029: the due-Task index."""

from __future__ import annotations

import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from sagewai.db.models import TaskModel


def test_revision_chain() -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.029_task_due_index")
    assert mod.revision == "029_task_due_index"
    assert mod.down_revision == "028_task_coordinator"


def test_the_orm_declares_the_same_index() -> None:
    names = {index.name: tuple(column.name for column in index.columns) for index in TaskModel.__table__.indexes}
    assert names["ix_tasks_scope_due"] == ("project_scope_key", "next_run_at")


def test_upgrade_creates_and_downgrade_drops_the_index(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.029_task_due_index")
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            TaskModel.__table__.create(conn)
            context = MigrationContext.configure(conn)
            monkeypatch.setattr(mod, "op", Operations(context))
            existing = {index["name"] for index in inspect(conn).get_indexes("tasks")}
            if "ix_tasks_scope_due" in existing:
                Operations(context).drop_index("ix_tasks_scope_due", table_name="tasks")
            mod.upgrade()
            assert "ix_tasks_scope_due" in {index["name"] for index in inspect(conn).get_indexes("tasks")}
            mod.downgrade()
            assert "ix_tasks_scope_due" not in {index["name"] for index in inspect(conn).get_indexes("tasks")}
    finally:
        engine.dispose()
