# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Collision tests for durable workflow internal identity encoding."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from sagewai.core.state import WorkflowRun
from sagewai.core.stores.sqlite_store import SqliteWorkflowStore


@pytest.mark.asyncio
async def test_scope_and_workflow_delimiters_cannot_alias(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}")
    store = SqliteWorkflowStore(engine)
    await store.initialize()
    try:
        left = WorkflowRun(
            workflow_name="w",
            run_id="same",
            project_id="a:b",
        )
        right = WorkflowRun(
            workflow_name="b:w",
            run_id="same",
            project_id="a",
        )
        await store.save_run(left)
        await store.save_run(right)

        assert await store.load_run(
            "w", "same", project_id="a:b"
        ) is not None
        assert await store.load_run(
            "b:w", "same", project_id="a"
        ) is not None
    finally:
        await engine.dispose()
