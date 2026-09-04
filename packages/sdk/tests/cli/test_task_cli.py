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

from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_a_single_org_home_reads_channels_from_the_state_file(wired, tmp_path) -> None:
    from sagewai.admin.channel_config_store import StateFileChannelConfigStore
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.cli import tasks as tasks_module

    store = await tasks_module._config_store(
        "project-a", AdminStateFile(path=tmp_path / "state.json")
    )

    assert isinstance(store, StateFileChannelConfigStore)


def test_the_tick_hands_its_config_store_to_the_resolver(wired, monkeypatch) -> None:
    from sagewai.admin.channel_config_store import StateFileChannelConfigStore
    from sagewai.cli import tasks as tasks_module

    seen = {}

    async def _capture(
        *, defaults, config_store=None, tracking_channel=None, console_base_url=None
    ):
        seen["config_store"] = config_store
        return ()

    monkeypatch.setattr(tasks_module, "build_decision_channels", _capture)
    result = wired.invoke(task_group, ["--project", "project-a", "tick"])

    assert result.exit_code == 0, result.output
    assert isinstance(seen["config_store"], StateFileChannelConfigStore)


def test_tick_wires_runners_to_connections_context_store(wired, monkeypatch) -> None:
    from sagewai.cli import tasks as tasks_module

    connection_store = object()
    credentials = object()
    captured: dict[str, tuple[object, object]] = {}
    monkeypatch.setattr(
        tasks_module,
        "build_connections_context",
        lambda _sf: SimpleNamespace(store=connection_store, router=credentials),
    )

    async def _tick(self):
        software = self._driver._profile_runners(SimpleNamespace(profile="software"))
        report = self._driver._profile_runners(SimpleNamespace(profile="report"))
        captured["connection_stores"] = (
            software._connection_store,
            report._connection_store,
        )
        return 0

    monkeypatch.setattr(tasks_module.TaskCoordinatorRunner, "tick", _tick)

    result = wired.invoke(task_group, ["--project", "project-a", "tick"])

    assert result.exit_code == 0, result.output
    assert all(store is connection_store for store in captured["connection_stores"])


def test_tick_closes_report_runner_when_software_close_fails(wired, monkeypatch) -> None:
    from sagewai.cli import tasks as tasks_module

    captured = {}

    class _Runner:
        def __init__(self, *, fail: bool = False, **_kwargs) -> None:
            self.fail = fail
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True
            if self.fail:
                raise RuntimeError("software close failed")

    def _software(**kwargs):
        captured["software"] = _Runner(fail=True, **kwargs)
        return captured["software"]

    def _report(**kwargs):
        captured["report"] = _Runner(**kwargs)
        return captured["report"]

    async def _tick(self):
        return 0

    monkeypatch.setattr(tasks_module, "SoftwareProfileRunner", _software)
    monkeypatch.setattr(tasks_module, "ReportProfileRunner", _report)
    monkeypatch.setattr(tasks_module.TaskCoordinatorRunner, "tick", _tick)

    result = wired.invoke(task_group, ["--project", "project-a", "tick"])

    assert result.exit_code != 0
    assert captured["software"].closed is True
    assert captured["report"].closed is True
