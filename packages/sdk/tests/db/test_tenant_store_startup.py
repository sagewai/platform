# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Startup wiring + fail-closed regressions for the tenant resource stores.

Covers two gaps a project member could otherwise hit in the default
``create_admin_serve_app(sf)`` multi-tenant path:

1. ``build_resource_stores(None)`` (the auto-build path — no injected identity
   store) must still give the provider store an ``IdentityStore`` so per-project
   secret encryption can resolve the project data key (regression for an
   ``AttributeError: NoneType has no get_project_data_key``).
2. The startup fail-closed check must refuse to serve when encrypted tenant
   provider secrets exist but the org master key cannot be resolved — the tenant
   analogue of ``sf.require_secret_key_if_encrypted()``.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from sagewai.admin import tenant_keys
from sagewai.admin.tenancy import RequestContext, UserRef


def _ctx(org_id, project_id):
    return RequestContext(
        actor=UserRef("u", "u"),
        org_id=org_id,
        project_id=project_id,
        roles=frozenset({"project:admin"}),
        scopes=frozenset({"read", "write", "admin"}),
        request_id="r",
        tenancy_mode="multi",
    )


@pytest.mark.asyncio
async def test_auto_built_provider_store_has_identity_for_project_keys(dialect_engine, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    key = Fernet.generate_key()  # ONE pinned key (fresh-per-call breaks encrypt/decrypt)
    monkeypatch.setattr(tenant_keys, "_master_key_source", lambda: (key, "test"))
    from sagewai.db import factory

    monkeypatch.setattr(factory, "get_engine", lambda: dialect_engine)

    from sagewai.admin.resource_stores import build_resource_stores

    rs = await build_resource_stores(None)  # the default path: no injected identity store
    assert rs is not None
    assert rs.provider._identity is not None  # would be None before the fix

    ident = rs.provider._identity
    oid = (await ident.bootstrap_org("Acme", "acme"))["id"]
    pid = (await ident.create_project(oid, "p", "P"))["id"]
    ctx = _ctx(oid, pid)
    out = await rs.provider.upsert(
        {"provider_name": "openai", "config": {"api_key": "sk-1"}}, ctx=ctx
    )
    full = await rs.provider.get_decrypted(out["id"], ctx=ctx)
    assert full["config"]["api_key"] == "sk-1"  # encrypted + decryptable, no AttributeError


@pytest.mark.asyncio
async def test_startup_fails_closed_when_encrypted_provider_and_key_missing(
    dialect_engine, monkeypatch
):
    from sagewai.admin.identity_store import IdentityStore
    from sagewai.admin.provider_store import PostgresProviderStore
    from sagewai.admin.serve import _require_tenant_provider_key_if_encrypted
    from sagewai.sealed.master_key import MasterKeyMissing

    key = Fernet.generate_key()
    monkeypatch.setattr(tenant_keys, "_master_key_source", lambda: (key, "test"))
    ident = IdentityStore(engine=dialect_engine)
    await ident.init()
    oid = (await ident.bootstrap_org("Acme", "acme"))["id"]
    pid = (await ident.create_project(oid, "p", "P"))["id"]
    store = PostgresProviderStore(engine=dialect_engine, identity_store=ident)
    await store.init()

    def _raise():
        raise MasterKeyMissing("no key")

    # No encrypted rows yet -> the check is a no-op even with the key unavailable.
    monkeypatch.setattr(tenant_keys, "_master_key_source", _raise)
    await _require_tenant_provider_key_if_encrypted(store)

    # Seed an encrypted provider (key available), then make the key unavailable.
    monkeypatch.setattr(tenant_keys, "_master_key_source", lambda: (key, "test"))
    await store.upsert(
        {"provider_name": "openai", "config": {"api_key": "sk-1"}}, ctx=_ctx(oid, pid)
    )
    monkeypatch.setattr(tenant_keys, "_master_key_source", _raise)
    with pytest.raises(MasterKeyMissing):
        await _require_tenant_provider_key_if_encrypted(store)  # fail closed

    # Key available again -> encrypted rows are fine; the backend may serve.
    monkeypatch.setattr(tenant_keys, "_master_key_source", lambda: (key, "test"))
    await _require_tenant_provider_key_if_encrypted(store)
