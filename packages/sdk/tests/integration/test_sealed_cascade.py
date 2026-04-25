# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""End-to-end: profile created → cascade resolved → run row carries effective keys."""
import os

import pytest


pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_DATABASE_URL"),
    reason="SAGEWAI_DATABASE_URL not set",
)


@pytest.mark.asyncio
async def test_user_kwarg_beats_workflow_beats_system(tmp_path, monkeypatch):
    """Three-level cascade end-to-end: system + workflow + user → user wins per key."""
    from cryptography.fernet import Fernet

    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.crypto import Crypto
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.refs import _BACKENDS

    profiles_path = tmp_path / "profiles.json"
    crypto = Crypto(Fernet.generate_key())
    backend = BuiltinAdminStoreBackend(profiles_path=profiles_path, crypto=crypto)
    monkeypatch.setitem(_BACKENDS, "builtin", backend)

    await backend.save_profile(ProfileWritePayload(
        id="sys", name="System",
        env={"SHARED": "system-value", "ONLY_SYS": "x"},
        secrets={"OPENAI_API_KEY": "sys-key"},
    ))
    await backend.save_profile(ProfileWritePayload(
        id="user", name="User",
        env={"SHARED": "user-value"},
        secrets={"OPENAI_API_KEY": "user-key"},
    ))

    from sagewai.sealed.audit import AuditWriter
    from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile
    from sagewai.core.stores.postgres import PostgresStore

    store = PostgresStore(database_url=os.environ["SAGEWAI_DATABASE_URL"])
    await store.initialize()
    audit_writer = AuditWriter(store)

    levels = [
        CascadeLevel(name="system", profile_ref="sys", overrides=None),
        CascadeLevel(name="workflow", profile_ref=None, overrides=None),
        CascadeLevel(name="user", profile_ref="user", overrides=None),
    ]
    eff = await resolve_security_profile(levels=levels, audit_writer=audit_writer)
    assert eff.env["SHARED"] == "user-value"
    assert eff.env["OPENAI_API_KEY"] == "user-key"
    assert eff.env["ONLY_SYS"] == "x"

    await store.close()
