# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for PostgresProviderStore — runs against both SQLite and Postgres.

Covers the pattern-setting tenant-scoped config store:
- per-project secret encryption at rest + redaction on list / decrypt on get
- project isolation + org-shared (global) inheritance
- a project may not delete an org-shared (global) row; the org may
- one-default-per-scope invariant on set_default

Uses the ``dialect_engine`` fixture from tests/db/conftest.py
(SQLite always; Postgres when SAGEWAI_TEST_DATABASE_URL is set). An
``IdentityStore`` is seeded on the SAME engine so tenant_keys can mint
per-project data keys; the org master key is pinned for deterministic crypto.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from sagewai.admin import tenant_keys
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.provider_store import (
    PostgresProviderStore,
    ProviderSecretDecryptionError,
)
from sagewai.admin.tenancy import RequestContext, UserRef
from sagewai.sealed.crypto import SecretCorrupted


def _ctx(project_id):
    return RequestContext(
        actor=UserRef("u", "u"),
        org_id="default",
        project_id=project_id,
        roles=frozenset({"org:admin"} if project_id is None else {"project:admin"}),
        scopes=frozenset({"read", "write", "admin"}),
        request_id="r",
        tenancy_mode="multi",
    )


@pytest.fixture
async def store(dialect_engine, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setattr(tenant_keys, "_master_key_source", lambda: (key, "test"))
    ident = IdentityStore(engine=dialect_engine)
    await ident.init()
    # Seed org "default" + projects "P" and "Q" via the real IdentityStore API.
    await ident.bootstrap_org("Default", "default", org_id="default")
    await ident.create_project("default", "p", "Project P", project_id="P")
    await ident.create_project("default", "q", "Project Q", project_id="Q")
    s = PostgresProviderStore(engine=dialect_engine, identity_store=ident)
    await s.init()
    return s


@pytest.mark.asyncio
async def test_secret_encrypted_at_rest_and_redacted_on_list(store):
    await store.upsert(
        {"provider_name": "openai", "config": {"api_key": "sk-secret"}}, ctx=_ctx("P")
    )
    listed = await store.list(ctx=_ctx("P"))
    cfg = listed[0]["config"]
    assert cfg.get("api_key_set") is True
    assert "api_key" not in cfg
    full = await store.get_decrypted(listed[0]["id"], ctx=_ctx("P"))
    assert full["config"]["api_key"] == "sk-secret"


@pytest.mark.asyncio
async def test_project_isolation_and_global_inheritance(store):
    await store.upsert({"provider_name": "shared", "config": {}}, ctx=_ctx(None))
    await store.upsert({"provider_name": "local", "config": {}}, ctx=_ctx("P"))
    names_p = sorted(p["provider_name"] for p in await store.list(ctx=_ctx("P")))
    assert names_p == ["local", "shared"]
    names_q = sorted(p["provider_name"] for p in await store.list(ctx=_ctx("Q")))
    assert names_q == ["shared"]


@pytest.mark.asyncio
async def test_project_cannot_delete_global(store):
    g = await store.upsert({"provider_name": "shared", "config": {}}, ctx=_ctx(None))
    assert await store.delete(g["id"], ctx=_ctx("P")) is False
    assert await store.delete(g["id"], ctx=_ctx(None)) is True


@pytest.mark.asyncio
async def test_one_default_per_scope(store):
    a = await store.upsert({"provider_name": "a", "config": {}, "default": True}, ctx=_ctx("P"))
    await store.upsert({"provider_name": "b", "config": {}, "default": True}, ctx=_ctx("P"))
    res = await store.set_default(a["id"], ctx=_ctx("P"))
    assert res is not None
    # exactly one default remains in scope P
    listed = await store.list(ctx=_ctx("P"))
    defaults = [p for p in listed if p.get("default")]
    assert len(defaults) == 1
    assert defaults[0]["id"] == a["id"]


@pytest.mark.asyncio
async def test_list_decrypted_is_scoped_and_decrypted(store):
    # list_decrypted backs model aggregation: it must return in-scope rows (own +
    # global) with secrets DECRYPTED (internal path), and never another project's.
    await store.upsert({"provider_name": "shared", "config": {}}, ctx=_ctx(None))
    await store.upsert({"provider_name": "openai", "config": {"api_key": "sk-P"}}, ctx=_ctx("P"))
    await store.upsert({"provider_name": "other", "config": {}}, ctx=_ctx("Q"))
    rows = await store.list_decrypted(ctx=_ctx("P"))
    names = sorted(r["provider_name"] for r in rows)
    assert names == ["openai", "shared"]  # own + global, never Q's
    openai = next(r for r in rows if r["provider_name"] == "openai")
    assert openai["config"]["api_key"] == "sk-P"  # decrypted on the internal path


@pytest.mark.asyncio
async def test_has_encrypted_secrets(store):
    # Drives the startup fail-closed check: false until an encrypted secret exists.
    assert await store.has_encrypted_secrets() is False
    await store.upsert({"provider_name": "plain", "config": {}}, ctx=_ctx("P"))
    assert await store.has_encrypted_secrets() is False
    await store.upsert({"provider_name": "withkey", "config": {"api_key": "sk-1"}}, ctx=_ctx("P"))
    assert await store.has_encrypted_secrets() is True


@pytest.mark.asyncio
async def test_corrupt_provider_secret_fails_closed(store, monkeypatch):
    await store.upsert(
        {"provider_name": "openai", "config": {"api_key": "sk-P"}},
        ctx=_ctx("P"),
    )

    async def _boom(*args, **kwargs):
        raise SecretCorrupted("tampered")

    monkeypatch.setattr(tenant_keys, "decrypt_for_project", _boom)
    with pytest.raises(ProviderSecretDecryptionError):
        await store.list_decrypted(ctx=_ctx("P"))
