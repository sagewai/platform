# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The coordinator reads the channel configs the admin actually writes, in either mode."""

from __future__ import annotations

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from sagewai.admin import tenant_keys
from sagewai.admin.admin_resource_store import AdminResourceStore
from sagewai.admin.channel_config_store import (
    AdminResourceChannelConfigStore,
    StateFileChannelConfigStore,
    org_for_project,
)
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tenancy import ALL_SCOPES, RequestContext, UserRef
from sagewai.db.engine import create_engine
from sagewai.work.tasks.channels import ChannelConfigStore, build_decision_channels
from sagewai.work.tasks.models import TaskDefaults

WEBHOOK = "https://hooks.slack.com/services/T/B/XXX"


@pytest_asyncio.fixture
async def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    master = Fernet.generate_key()
    monkeypatch.setattr(tenant_keys, "_master_key_source", lambda: (master, "test"))
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'id.db'}")
    identity = IdentityStore(engine=engine)
    await identity.init()
    org_id = (await identity.bootstrap_org("Acme", "acme"))["id"]
    project_id = (await identity.create_project(org_id, "pa", "PA"))["id"]
    other_id = (await identity.create_project(org_id, "pb", "PB"))["id"]
    foreign_org_id = (await identity.bootstrap_org("Beta", "beta"))["id"]
    foreign_project_id = (await identity.create_project(foreign_org_id, "pc", "PC"))["id"]
    resources = AdminResourceStore(engine=engine)
    await resources.init()
    yield {
        "identity": identity,
        "resources": resources,
        "org_id": org_id,
        "project_id": project_id,
        "other_id": other_id,
        "foreign_org_id": foreign_org_id,
        "foreign_project_id": foreign_project_id,
    }
    await engine.dispose()


def _ctx(org_id: str, project_id: str | None) -> RequestContext:
    return RequestContext(
        actor=UserRef(id="u", label="u@acme.io"),
        org_id=org_id,
        project_id=project_id,
        roles=frozenset({"org:admin"}),
        scopes=ALL_SCOPES,
        request_id="r",
        tenancy_mode="multi",
    )


async def _write_channel(
    wired,
    project_id,
    *,
    org_id=None,
    channel_id="ch-1",
    webhook=WEBHOOK,
    channel_type="slack",
):
    org_id = wired["org_id"] if org_id is None else org_id
    encrypted = await tenant_keys.encrypt_for_project(
        wired["identity"], org_id, project_id, webhook
    )
    await wired["resources"].upsert_for(
        _ctx(org_id, project_id),
        "notification_channel",
        channel_id,
        {
            "id": channel_id,
            "project_id": project_id,
            "channel_type": channel_type,
            "enabled": True,
            "webhook_url": encrypted,
        },
    )


def _tenant_store(wired) -> AdminResourceChannelConfigStore:
    return AdminResourceChannelConfigStore(
        resource_store=wired["resources"], identity_store=wired["identity"], org_id=wired["org_id"]
    )


def _state_file(tmp_path, rows: list[dict]) -> AdminStateFile:
    state = AdminStateFile(path=tmp_path / "state.json")
    state.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")
    state.mutate(lambda data: data.__setitem__("notification_channels", rows))
    return state


@pytest.mark.parametrize("builder", ["tenant", "state_file"])
def test_both_adapters_satisfy_the_resolver_protocol(builder, tmp_path) -> None:
    store = (
        StateFileChannelConfigStore(state_file=_state_file(tmp_path, []))
        if builder == "state_file"
        else AdminResourceChannelConfigStore(
            resource_store=object(), identity_store=object(), org_id="o"
        )
    )
    assert isinstance(store, ChannelConfigStore)


@pytest.mark.asyncio
async def test_the_tenant_adapter_decrypts_the_projects_own_webhook(wired) -> None:
    await _write_channel(wired, wired["project_id"])

    configs = await _tenant_store(wired).list_channel_configs(wired["project_id"])

    assert [config["channel_type"] for config in configs] == ["slack"]
    assert configs[0]["webhook_url"] == WEBHOOK


@pytest.mark.asyncio
async def test_another_projects_webhook_is_not_visible(wired) -> None:
    await _write_channel(wired, wired["other_id"])

    assert await _tenant_store(wired).list_channel_configs(wired["project_id"]) == []


@pytest.mark.asyncio
async def test_foreign_project_row_is_excluded_by_the_project_filter(wired) -> None:
    await _write_channel(
        wired,
        wired["foreign_project_id"],
        org_id=wired["foreign_org_id"],
        channel_id="ch-foreign",
    )

    assert await _tenant_store(wired).list_channel_configs(wired["project_id"]) == []


@pytest.mark.asyncio
async def test_an_org_shared_webhook_is_inherited(wired) -> None:
    await _write_channel(wired, None, channel_id="ch-shared")

    configs = await _tenant_store(wired).list_channel_configs(wired["project_id"])

    assert [config["id"] for config in configs] == ["ch-shared"]
    assert configs[0]["webhook_url"] == WEBHOOK


@pytest.mark.asyncio
async def test_a_corrupt_secret_is_skipped_without_stopping_the_resolver(wired, caplog) -> None:
    await wired["resources"].upsert_for(
        _ctx(wired["org_id"], wired["project_id"]),
        "notification_channel",
        "ch-bad",
        {
            "id": "ch-bad",
            "project_id": wired["project_id"],
            "channel_type": "slack",
            "enabled": True,
            "webhook_url": "fernet:not-a-real-token",
        },
    )
    await _write_channel(wired, wired["project_id"], channel_id="ch-ok")
    store = _tenant_store(wired)

    with caplog.at_level("WARNING"):
        configs = await store.list_channel_configs(wired["project_id"])
        skipped = [
            record for record in caplog.records if record.msg.startswith("channel config could not")
        ]
        assert [record.channel_id for record in skipped] == ["ch-bad"]
        channels = await build_decision_channels(
            defaults=TaskDefaults(
                project_id=wired["project_id"], decision_channels=("console", "slack_webhook")
            ),
            config_store=store,
        )

    assert [config["id"] for config in configs] == ["ch-ok"]
    assert [channel.name for channel in channels] == ["console", "slack_webhook"]
    skipped = [
        record for record in caplog.records if record.msg.startswith("channel config could not")
    ]
    assert [record.channel_id for record in skipped] == ["ch-bad", "ch-bad"]
    assert [record.channel_type for record in skipped] == ["slack", "slack"]
    assert "fernet:" not in caplog.text


@pytest.mark.asyncio
async def test_a_malformed_row_is_skipped(wired, caplog) -> None:
    await wired["resources"].upsert_for(
        _ctx(wired["org_id"], wired["project_id"]),
        "notification_channel",
        "ch-empty",
        {"id": "ch-empty", "project_id": wired["project_id"]},
    )
    await _write_channel(wired, wired["project_id"], channel_id="ch-ok")

    with caplog.at_level("WARNING"):
        configs = await _tenant_store(wired).list_channel_configs(wired["project_id"])

    assert [config["id"] for config in configs] == ["ch-ok"]
    malformed = [
        record for record in caplog.records if record.msg.startswith("channel config is malformed")
    ]
    assert [record.channel_id for record in malformed] == ["ch-empty"]


@pytest.mark.asyncio
async def test_the_resolver_builds_a_slack_channel_from_the_tenant_adapter(wired) -> None:
    await _write_channel(wired, wired["project_id"])

    channels = await build_decision_channels(
        defaults=TaskDefaults(
            project_id=wired["project_id"], decision_channels=("console", "slack_webhook")
        ),
        config_store=_tenant_store(wired),
    )

    assert [channel.name for channel in channels] == ["console", "slack_webhook"]
    assert channels[1]._webhook_url == WEBHOOK


@pytest.mark.asyncio
async def test_the_state_file_adapter_reads_the_projects_and_the_shared_rows(tmp_path) -> None:
    state = _state_file(
        tmp_path,
        [
            {"id": "ch-mine", "project_id": "pa", "channel_type": "slack", "webhook_url": WEBHOOK},
            {
                "id": "ch-shared",
                "project_id": None,
                "channel_type": "google_chat",
                "webhook_url": WEBHOOK,
            },
            {
                "id": "ch-theirs",
                "project_id": "pb",
                "channel_type": "slack",
                "webhook_url": WEBHOOK,
            },
        ],
    )

    configs = await StateFileChannelConfigStore(state_file=state).list_channel_configs("pa")

    assert [config["id"] for config in configs] == ["ch-mine", "ch-shared"]
    assert configs[0]["webhook_url"] == WEBHOOK


@pytest.mark.asyncio
async def test_the_state_file_adapter_resolves_a_channel_the_admin_route_wrote(tmp_path) -> None:
    state = _state_file(
        tmp_path,
        [{"id": "ch-1", "project_id": "pa", "channel_type": "slack", "webhook_url": WEBHOOK}],
    )

    channels = await build_decision_channels(
        defaults=TaskDefaults(project_id="pa", decision_channels=("console", "slack_webhook")),
        config_store=StateFileChannelConfigStore(state_file=state),
    )

    assert [channel.name for channel in channels] == ["console", "slack_webhook"]


@pytest.mark.asyncio
async def test_org_for_project_finds_the_owning_organization(wired) -> None:
    assert await org_for_project(wired["identity"], wired["project_id"]) == wired["org_id"]
    assert await org_for_project(wired["identity"], "no-such-project") is None
