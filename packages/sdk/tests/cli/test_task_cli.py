# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""sagewai task create writes one Task; sagewai task tick runs exactly one tick."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from sagewai.cli.tasks import task_group
from sagewai.work.tasks.models import TaskDefaults
from sagewai.work.tasks.store import TaskStore
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _task

BRIEF = (
    "Implement the retry queue in the payments service repository, add the failing test first, "
    "and open a pull request when the deterministic verification command passes."
)


@pytest.fixture
def wired(dialect_engine, monkeypatch, tmp_path):  # noqa: F811
    from sagewai.cli import tasks as tasks_module

    async def _ensure_schema() -> None:
        store = TaskStore(engine=dialect_engine)
        await store.init()
        await store.put_defaults(
            TaskDefaults(project_id="project-a", target=_task().target), expected_revision=0
        )

    monkeypatch.setattr(tasks_module.factory, "ensure_schema", _ensure_schema)
    monkeypatch.setattr(tasks_module.factory, "get_engine", lambda: dialect_engine)
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    return CliRunner()


def test_create_prints_the_task_id(wired) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "create", BRIEF])
    assert result.exit_code == 0, result.output
    assert len(result.output.strip()) == 36


def test_create_reads_a_brief_file(wired, tmp_path) -> None:
    path = tmp_path / "brief.md"
    path.write_text(BRIEF, encoding="utf-8")
    result = wired.invoke(task_group, ["--project", "project-a", "create", "--file", str(path)])
    assert result.exit_code == 0, result.output


def test_tick_on_an_empty_project_drives_nothing(wired) -> None:
    result = wired.invoke(task_group, ["--project", "project-a", "tick"])
    assert result.exit_code == 0, result.output
    assert "0" in result.output
