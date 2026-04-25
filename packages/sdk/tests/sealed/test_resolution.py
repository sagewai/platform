# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for Sealed-i cascade resolution."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.sealed.audit import AuditWriter
from sagewai.sealed.models import Profile
from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile


class _FakeBackend:
    name = scheme = "builtin"

    def __init__(self):
        self.profiles: dict[str, Profile] = {}

    async def get_profile(self, pid: str) -> Profile:
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
def fake_backend():
    from sagewai.sealed.refs import register_backend
    fb = _FakeBackend()
    register_backend(fb)
    return fb


@pytest.mark.asyncio
async def test_per_key_merge_user_wins(fake_backend, fake_audit):
    fake_backend.profiles["sys"] = Profile(
        id="sys", name="System",
        env={"SHARED": "system-value", "ONLY_SYS": "x"},
        secret_keys=["OPENAI_API_KEY"],
        secrets={"OPENAI_API_KEY": "sys-key"},
    )
    fake_backend.profiles["user"] = Profile(
        id="user", name="User",
        env={"SHARED": "user-value"},
        secret_keys=["OPENAI_API_KEY"],
        secrets={"OPENAI_API_KEY": "user-key"},
    )

    levels = [
        CascadeLevel(name="system", profile_ref="sys", overrides=None),
        CascadeLevel(name="workflow", profile_ref=None, overrides=None),
        CascadeLevel(name="user", profile_ref="user", overrides=None),
    ]
    eff = await resolve_security_profile(levels=levels, audit_writer=fake_audit)
    assert eff.env["SHARED"] == "user-value"
    assert eff.env["OPENAI_API_KEY"] == "user-key"
    assert eff.env["ONLY_SYS"] == "x"
    assert "OPENAI_API_KEY" in eff.secret_keys
    assert eff.cascade_origins["SHARED"] == "user"
    assert eff.cascade_origins["ONLY_SYS"] == "system"


@pytest.mark.asyncio
async def test_inline_overrides_apply_after_profile(fake_backend, fake_audit):
    fake_backend.profiles["sys"] = Profile(
        id="sys", name="System",
        env={"DEBUG": "0"},
        secrets={},
    )
    levels = [
        CascadeLevel(
            name="system",
            profile_ref="sys",
            overrides={"DEBUG": "1", "EXTRA": "yes"},
        ),
    ]
    eff = await resolve_security_profile(levels=levels, audit_writer=fake_audit)
    assert eff.env["DEBUG"] == "1"
    assert eff.env["EXTRA"] == "yes"
    assert eff.cascade_origins["DEBUG"] == "system_override"
    assert eff.cascade_origins["EXTRA"] == "system_override"


@pytest.mark.asyncio
async def test_empty_string_tombstone_removes_key(fake_backend, fake_audit):
    fake_backend.profiles["sys"] = Profile(
        id="sys", name="System",
        env={"DEBUG": "0", "KEEP": "yes"},
        secrets={},
    )
    levels = [
        CascadeLevel(
            name="system",
            profile_ref="sys",
            overrides={"DEBUG": ""},
        ),
    ]
    eff = await resolve_security_profile(levels=levels, audit_writer=fake_audit)
    assert "DEBUG" not in eff.env
    assert eff.env["KEEP"] == "yes"


@pytest.mark.asyncio
async def test_allowed_workflows_blocks_unauthorized(fake_backend, fake_audit):
    fake_backend.profiles["sys"] = Profile(
        id="sys", name="System",
        allowed_workflows=["billing-pipeline"],
        env={"X": "1"},
        secrets={},
    )
    levels = [CascadeLevel(name="system", profile_ref="sys", overrides=None)]
    with pytest.raises(PermissionError, match="not allowed"):
        await resolve_security_profile(
            levels=levels,
            audit_writer=fake_audit,
            audit_context={"workflow_name": "report-pipeline"},
        )
