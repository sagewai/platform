# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Notification secret decrypt uses tenant keys and fails closed."""

from __future__ import annotations

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from sagewai.admin import tenant_keys
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.notification_secrets import (
    NOTIFICATION_SECRET_KEYS,
    NotificationSecretDecryptionError,
    decrypt_notification_secrets,
)
from tests.db.conftest import dialect_engine  # noqa: F401

WEBHOOK = "https://hooks.slack.com/services/T000/B000/secret"


@pytest_asyncio.fixture
async def wired(dialect_engine, monkeypatch):  # noqa: F811
    master = Fernet.generate_key()
    monkeypatch.setattr(tenant_keys, "_master_key_source", lambda: (master, "test"))

    identity = IdentityStore(engine=dialect_engine)
    await identity.init()
    org_id = (await identity.bootstrap_org("Acme", "acme"))["id"]
    project_id = (await identity.create_project(org_id, "pa", "PA"))["id"]
    other_project_id = (await identity.create_project(org_id, "pb", "PB"))["id"]

    return {
        "identity": identity,
        "org_id": org_id,
        "project_id": project_id,
        "other_project_id": other_project_id,
    }


@pytest.mark.asyncio
async def test_a_project_secret_round_trips(wired) -> None:
    encrypted = await tenant_keys.encrypt_for_project(
        wired["identity"], wired["org_id"], wired["project_id"], WEBHOOK
    )

    decrypted = await decrypt_notification_secrets(
        {"project_id": wired["project_id"], "channel_type": "slack", "webhook_url": encrypted},
        identity_store=wired["identity"],
        org_id=wired["org_id"],
    )

    assert decrypted["webhook_url"] == WEBHOOK
    assert decrypted["channel_type"] == "slack"


@pytest.mark.asyncio
async def test_an_org_shared_secret_decrypts_under_the_org_key(wired) -> None:
    encrypted = await tenant_keys.encrypt_for_project(
        wired["identity"], wired["org_id"], None, WEBHOOK
    )

    decrypted = await decrypt_notification_secrets(
        {"project_id": None, "channel_type": "slack", "webhook_url": encrypted},
        identity_store=wired["identity"],
        org_id=wired["org_id"],
    )

    assert decrypted["webhook_url"] == WEBHOOK


@pytest.mark.asyncio
async def test_decrypt_leaves_a_plain_value_alone(wired) -> None:
    decrypted = await decrypt_notification_secrets(
        {"project_id": wired["project_id"], "webhook_url": WEBHOOK, "channel_type": "slack"},
        identity_store=wired["identity"],
        org_id=wired["org_id"],
    )

    assert decrypted["webhook_url"] == WEBHOOK


@pytest.mark.asyncio
async def test_a_corrupt_secret_raises_and_never_returns_the_ciphertext(wired) -> None:
    with pytest.raises(NotificationSecretDecryptionError) as excinfo:
        await decrypt_notification_secrets(
            {
                "project_id": wired["project_id"],
                "channel_type": "slack",
                "webhook_url": "fernet:not-a-real-token",
            },
            identity_store=wired["identity"],
            org_id=wired["org_id"],
        )

    assert "fernet:" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_another_projects_ciphertext_does_not_decrypt(wired) -> None:
    """Project B holds its own data key, so A's ciphertext must fail closed under it."""
    encrypted = await tenant_keys.encrypt_for_project(
        wired["identity"], wired["org_id"], wired["project_id"], WEBHOOK
    )
    await tenant_keys.encrypt_for_project(
        wired["identity"], wired["org_id"], wired["other_project_id"], "seed"
    )

    with pytest.raises(NotificationSecretDecryptionError):
        await decrypt_notification_secrets(
            {
                "project_id": wired["other_project_id"],
                "channel_type": "slack",
                "webhook_url": encrypted,
            },
            identity_store=wired["identity"],
            org_id=wired["org_id"],
        )


@pytest.mark.asyncio
async def test_only_the_declared_secret_keys_are_touched(wired) -> None:
    encrypted = await tenant_keys.encrypt_for_project(
        wired["identity"], wired["org_id"], wired["project_id"], WEBHOOK
    )

    decrypted = await decrypt_notification_secrets(
        {
            "project_id": wired["project_id"],
            "channel_type": "slack",
            "webhook_url": encrypted,
            "label": encrypted,
        },
        identity_store=wired["identity"],
        org_id=wired["org_id"],
    )

    assert decrypted["webhook_url"] == WEBHOOK
    assert decrypted["label"] == encrypted


def test_the_secret_keys_are_the_ones_serve_redacts() -> None:
    assert "webhook_url" in NOTIFICATION_SECRET_KEYS
    assert NOTIFICATION_SECRET_KEYS == frozenset(
        {"webhook_url", "email_api_key", "smtp_password", "api_key", "token", "secret", "password"}
    )
