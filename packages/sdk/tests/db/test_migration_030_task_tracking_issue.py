# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 030 adds one nullable projection column for the Task's tracking issue."""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from sagewai.db.models import TaskModel


def test_migration_030_revision_chain() -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.030_task_tracking_issue")
    assert mod.revision == "030_task_tracking_issue"
    assert mod.down_revision == "029_task_due_index"
    assert callable(mod.upgrade) and callable(mod.downgrade)


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_the_tracking_issue_column_is_nullable_text(dialect) -> None:
    ddl = str(CreateTable(TaskModel.__table__).compile(dialect=dialect))
    assert "tracking_issue_url" in ddl
    assert "tracking_issue_url TEXT NOT NULL" not in ddl
