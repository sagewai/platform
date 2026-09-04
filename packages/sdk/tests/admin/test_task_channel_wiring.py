# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The app lifespan wires the coordinator to the active admin channel store."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sagewai.admin.channel_config_store import (
    AdminResourceChannelConfigStore,
    StateFileChannelConfigStore,
)
from tests.admin.test_fleet_reaper_wiring import _app, _isolate  # noqa: F401


def _capture_resolver_store(monkeypatch) -> dict[str, object]:
    from sagewai.work.tasks import channels as channels_module

    captured: dict[str, object] = {}

    async def _capture(
        *, defaults, config_store=None, tracking_channel=None, console_base_url=None
    ):
        captured["config_store"] = config_store
        return ()

    monkeypatch.setattr(channels_module, "build_decision_channels", _capture)
    return captured


@pytest.mark.asyncio
async def test_lifespan_wires_tenant_channel_config_store(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    from sagewai.admin.admin_resource_store import AdminResourceStore
    from sagewai.admin.identity_store import IdentityStore
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.db.engine import create_engine

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}")
    identity = IdentityStore(engine=engine)
    await identity.init()
    org_id = (await identity.bootstrap_org("Acme", "acme"))["id"]
    project_id = (await identity.create_project(org_id, "pa", "PA"))["id"]
    resources = AdminResourceStore(engine=engine)
    await resources.init()
    state_file = AdminStateFile(path=tmp_path / "state.json")
    state_file.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")
    app = create_admin_serve_app(
        state_file, identity_store=identity, admin_resource_store=resources
    )
    captured = _capture_resolver_store(monkeypatch)

    try:
        with TestClient(app):
            runner = app.state.task_coordinator_runner
            assert await runner._list_project_ids() == [project_id]
            await runner._driver._channel_factory(project_id)
    finally:
        await engine.dispose()

    store = captured["config_store"]
    assert isinstance(store, AdminResourceChannelConfigStore)
    assert store._org_id == org_id


@pytest.mark.asyncio
async def test_lifespan_wires_state_file_channel_config_store(tmp_path, monkeypatch) -> None:
    app, _token = _app(tmp_path)
    captured = _capture_resolver_store(monkeypatch)

    with TestClient(app):
        runner = app.state.task_coordinator_runner
        [project] = await runner._list_project_ids()
        await runner._driver._channel_factory(project)

    assert isinstance(captured["config_store"], StateFileChannelConfigStore)
