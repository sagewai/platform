# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sealed-iii.A — fail-closed property tests.

If the registry can't be consulted, no enqueue and no injection proceed.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.sealed.audit import AuditWriter


def _fake_audit() -> AuditWriter:
    fake_store = MagicMock()
    fake_store._pool = MagicMock()
    fake_store._pool.execute = AsyncMock()
    return AuditWriter(fake_store)


@pytest.mark.asyncio
async def test_resolver_fails_closed_when_registry_raises(monkeypatch, tmp_path):
    """If find_active_for_keys raises, resolve_security_profile must raise."""
    from cryptography.fernet import Fernet

    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.crypto import Crypto
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.refs import _BACKENDS, register_backend
    from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile
    from sagewai.sealed.revocation import RevocationCheckUnavailableError

    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=Crypto(Fernet.generate_key()),
        audit_writer=None,
    )
    _BACKENDS.clear()
    register_backend(backend)
    await backend.save_profile(ProfileWritePayload(
        id="acme", name="A", secrets={"K": "v"},
    ))

    class _BrokenRegistry:
        async def find_active_for_keys(self, *, profile_id, secret_keys):
            raise ConnectionError("postgres unreachable")

    levels = [CascadeLevel(name="user", profile_ref="acme", overrides=None)]
    with pytest.raises(RevocationCheckUnavailableError):
        await resolve_security_profile(
            levels=levels,
            audit_writer=_fake_audit(),
            revocation_registry=_BrokenRegistry(),
        )


@pytest.mark.asyncio
async def test_provider_fails_closed_when_registry_raises(monkeypatch, tmp_path):
    """env_for must raise RevocationCheckUnavailableError, not silently skip."""
    from cryptography.fernet import Fernet

    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.crypto import Crypto
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.provider import SealedSecretProvider
    from sagewai.sealed.refs import _BACKENDS, register_backend
    from sagewai.sealed.resolution import CascadeLevel
    from sagewai.sealed.revocation import RevocationCheckUnavailableError

    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=Crypto(Fernet.generate_key()),
        audit_writer=None,
    )
    _BACKENDS.clear()
    register_backend(backend)
    await backend.save_profile(ProfileWritePayload(
        id="acme", name="A", secrets={"K": "v"},
    ))

    class _BrokenRegistry:
        async def find_active_for_keys(self, *, profile_id, secret_keys):
            raise ConnectionError("db down")

    p = SealedSecretProvider(_fake_audit(), revocation_registry=_BrokenRegistry())
    with pytest.raises(RevocationCheckUnavailableError):
        await p.env_for(
            project_id="x", run_id="r", agent_id=None, declared_scopes=[],
            sealed_levels=[CascadeLevel(name="user", profile_ref="acme", overrides=None)],
            security_profile_ref="acme",
            effective_env_keys=["K"],
            effective_secret_keys=["K"],
        )
