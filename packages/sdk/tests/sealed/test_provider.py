# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for SealedSecretProvider."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.sealed.audit import AuditWriter
from sagewai.sealed.provider import SealedSecretProvider


def _fake_audit() -> AuditWriter:
    fake_store = MagicMock()
    fake_store._pool = MagicMock()
    fake_store._pool.execute = AsyncMock()
    return AuditWriter(fake_store)


@pytest.mark.asyncio
async def test_no_sealed_levels_returns_empty():
    p = SealedSecretProvider(_fake_audit())
    env = await p.env_for(
        project_id="x", run_id="r", agent_id=None, declared_scopes=[],
        sealed_levels=None,
    )
    assert env == {}


@pytest.mark.asyncio
async def test_sealed_levels_resolves_and_injects(monkeypatch, tmp_path):
    """When sealed_levels is provided, env is resolved + emit events fire."""
    from cryptography.fernet import Fernet

    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.crypto import Crypto
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.resolution import CascadeLevel

    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=Crypto(Fernet.generate_key()),
        audit_writer=None,
    )
    monkeypatch.setattr("sagewai.sealed.refs._BACKENDS", {"builtin": backend})
    await backend.save_profile(ProfileWritePayload(
        id="acme",
        name="Acme",
        secrets={"OPENAI_API_KEY": "sk-secret"},
        env={"DEBUG": "1"},
    ))

    audit = _fake_audit()
    p = SealedSecretProvider(audit)
    env = await p.env_for(
        project_id="proj",
        run_id="run-1",
        agent_id=None,
        declared_scopes=[],
        security_profile_ref="acme",
        effective_env_keys=["OPENAI_API_KEY", "DEBUG"],
        effective_secret_keys=["OPENAI_API_KEY"],
        sealed_levels=[CascadeLevel(name="user", profile_ref="acme", overrides=None)],
    )
    assert env["OPENAI_API_KEY"] == "sk-secret"
    assert env["DEBUG"] == "1"

    # profile.injected emitted (search audit calls)
    inj_calls = [
        c for c in audit._pool.execute.await_args_list
        if c.args[1] == "profile.injected"
    ]
    assert len(inj_calls) == 1


@pytest.mark.asyncio
async def test_drift_detection_emits_event(monkeypatch, tmp_path):
    """When current keys differ from committed, emit profile.drift_at_injection."""
    from cryptography.fernet import Fernet

    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.crypto import Crypto
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.resolution import CascadeLevel

    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=Crypto(Fernet.generate_key()),
        audit_writer=None,
    )
    monkeypatch.setattr("sagewai.sealed.refs._BACKENDS", {"builtin": backend})
    await backend.save_profile(ProfileWritePayload(
        id="acme",
        name="Acme",
        secrets={"NEW_KEY": "v"},
        env={},
    ))

    audit = _fake_audit()
    p = SealedSecretProvider(audit)
    # committed_keys says only OLD_KEY existed; current resolution has only NEW_KEY
    await p.env_for(
        project_id="x",
        run_id="r",
        agent_id=None,
        declared_scopes=[],
        security_profile_ref="acme",
        effective_env_keys=["OLD_KEY"],
        effective_secret_keys=["OLD_KEY"],
        sealed_levels=[CascadeLevel(name="user", profile_ref="acme", overrides=None)],
    )
    drift_calls = [
        c for c in audit._pool.execute.await_args_list
        if c.args[1] == "profile.drift_at_injection"
    ]
    assert len(drift_calls) == 1


@pytest.mark.asyncio
async def test_env_for_raises_secret_revoked_when_registry_blocks(monkeypatch, tmp_path):
    """Sandbox-start defense: if a key is now revoked, env_for raises."""
    from datetime import datetime, timezone

    from cryptography.fernet import Fernet

    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.crypto import Crypto
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.resolution import CascadeLevel
    from sagewai.sealed.revocation import Revocation, SecretRevokedError

    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=Crypto(Fernet.generate_key()),
        audit_writer=None,
    )
    monkeypatch.setattr("sagewai.sealed.refs._BACKENDS", {"builtin": backend})
    await backend.save_profile(ProfileWritePayload(
        id="acme", name="A", secrets={"K": "v"}, env={},
    ))

    class _StubRegistry:
        async def find_active_for_keys(self, *, profile_id, secret_keys):
            return {
                "K": Revocation(
                    id=99, profile_id=profile_id, secret_key="K",
                    revoked_at=datetime.now(timezone.utc),
                    reason="leaked", hard=False,
                )
            }

    audit = _fake_audit()
    p = SealedSecretProvider(audit, revocation_registry=_StubRegistry())
    with pytest.raises(SecretRevokedError):
        await p.env_for(
            project_id="x", run_id="r", agent_id=None, declared_scopes=[],
            security_profile_ref="acme",
            effective_env_keys=["K"],
            effective_secret_keys=["K"],
            sealed_levels=[CascadeLevel(name="user", profile_ref="acme", overrides=None)],
        )
