# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for cascade resolver with revocation check."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.sealed.audit import AuditWriter
from sagewai.sealed.models import Profile
from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile
from sagewai.sealed.revocation import (
    Revocation,
    SecretRevokedError,
)


class _StubBackend:
    name = scheme = "builtin"

    def __init__(self):
        self.profiles: dict[str, Profile] = {}

    async def get_profile(self, pid):
        return self.profiles[pid]

    async def get_profile_metadata(self, pid):
        raise NotImplementedError

    async def list_profiles(self):
        return []

    async def save_profile(self, p):
        raise NotImplementedError

    async def delete_profile(self, pid):
        raise NotImplementedError

    async def supports_master_key_rotation(self):
        return True

    async def rotate_master_key(self, k):
        return 0


@pytest.fixture
def fake_audit():
    fake_store = MagicMock()
    fake_store._pool = MagicMock()
    fake_store._pool.execute = AsyncMock()
    return AuditWriter(fake_store)


@pytest.fixture(autouse=True)
def clear_registry():
    from sagewai.sealed.refs import _BACKENDS
    saved = _BACKENDS.copy()
    _BACKENDS.clear()
    yield
    _BACKENDS.clear()
    _BACKENDS.update(saved)


@pytest.fixture
def stub_backend():
    from sagewai.sealed.refs import register_backend
    fb = _StubBackend()
    register_backend(fb)
    return fb


class _StubRegistry:
    def __init__(self, blocked: dict[tuple[str, str], Revocation]) -> None:
        self._blocked = blocked

    async def find_active_for_keys(self, *, profile_id, secret_keys):
        return {
            k: r for k, r in (
                (sk, self._blocked.get((profile_id, sk))) for sk in secret_keys
            ) if r is not None
        }


@pytest.mark.asyncio
async def test_resolver_raises_when_secret_is_revoked(stub_backend, fake_audit):
    stub_backend.profiles["acme"] = Profile(
        id="acme", name="Acme",
        env={"NORMAL": "v"},
        secrets={"OPENAI_API_KEY": "sk-..."},
        secret_keys=["OPENAI_API_KEY"],
    )
    revocation = Revocation(
        id=1, profile_id="acme", secret_key="OPENAI_API_KEY",
        revoked_at=datetime.now(timezone.utc), reason="leaked", hard=False,
    )
    registry = _StubRegistry({("acme", "OPENAI_API_KEY"): revocation})

    levels = [CascadeLevel(name="user", profile_ref="acme", overrides=None)]
    with pytest.raises(SecretRevokedError) as ei:
        await resolve_security_profile(
            levels=levels,
            audit_writer=fake_audit,
            revocation_registry=registry,
        )
    assert ei.value.profile_id == "acme"
    assert ei.value.secret_key == "OPENAI_API_KEY"
    assert ei.value.revocation_id == 1


@pytest.mark.asyncio
async def test_resolver_passes_when_no_active_revocation(stub_backend, fake_audit):
    stub_backend.profiles["acme"] = Profile(
        id="acme", name="Acme",
        env={"NORMAL": "v"},
        secrets={"OPENAI_API_KEY": "sk-..."},
        secret_keys=["OPENAI_API_KEY"],
    )
    registry = _StubRegistry({})  # no active revocations
    levels = [CascadeLevel(name="user", profile_ref="acme", overrides=None)]
    eff = await resolve_security_profile(
        levels=levels,
        audit_writer=fake_audit,
        revocation_registry=registry,
    )
    assert "OPENAI_API_KEY" in eff.env


@pytest.mark.asyncio
async def test_resolver_no_registry_skips_check(stub_backend, fake_audit):
    """Backwards compat: callers that don't pass registry skip the check."""
    stub_backend.profiles["acme"] = Profile(
        id="acme", name="Acme",
        secrets={"K": "v"},
        secret_keys=["K"],
    )
    levels = [CascadeLevel(name="user", profile_ref="acme", overrides=None)]
    eff = await resolve_security_profile(
        levels=levels,
        audit_writer=fake_audit,
        # revocation_registry=None
    )
    assert "K" in eff.env
