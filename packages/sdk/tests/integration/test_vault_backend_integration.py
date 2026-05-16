# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Integration test for VaultBackend against a live Vault dev server.

Gated on VAULT_ADDR + VAULT_TOKEN env. CI skips silently. Developers
run locally via:

    docker compose -f docker-compose.test-vault.yml up -d
    export VAULT_ADDR=http://localhost:8200
    export VAULT_TOKEN=test-root-token
    cd packages/sdk
    uv run pytest tests/integration/test_vault_backend_integration.py -v
    docker compose -f docker-compose.test-vault.yml down
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock

import pytest

VAULT_ADDR = os.environ.get("VAULT_ADDR")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN")

pytestmark = pytest.mark.skipif(
    not (VAULT_ADDR and VAULT_TOKEN),
    reason="VAULT_ADDR + VAULT_TOKEN not set; skipping live integration test",
)


@pytest.fixture
def backend():
    from sagewai.sealed.vault_backend import VaultBackend

    audit = MagicMock()
    audit.emit_calls = []

    async def _emit(**kwargs):
        audit.emit_calls.append(kwargs)

    audit.emit = _emit

    b = VaultBackend(
        addr=VAULT_ADDR,
        namespace=None,
        auth_method="token",
        auth_config={"token": VAULT_TOKEN},
        mount="secret",
        audit_writer=audit,
        path_prefix=f"sagewai-test-{uuid.uuid4().hex[:8]}",
    )
    return b, audit


@pytest.mark.asyncio
async def test_round_trip_against_live_vault(backend):
    from sagewai.sealed.backend import ProfileNotFoundError
    from sagewai.sealed.models import ProfileWritePayload

    b, audit = backend
    profile_id = "integration-acme"

    saved = await b.save_profile(
        ProfileWritePayload(
            id=profile_id,
            name="Integration Acme",
            description="live test",
            tags=["test"],
            env={"DEBUG": "0"},
            secrets={"OPENAI_API_KEY": "sk-integration"},
            allowed_workflows=["test-wf"],
        )
    )
    assert saved.name == "Integration Acme"

    metas = await b.list_profiles()
    assert any(m.id == profile_id for m in metas)

    got = await b.get_profile(profile_id)
    assert got.name == "Integration Acme"
    assert got.secrets == {"OPENAI_API_KEY": "sk-integration"}
    assert got.env == {"DEBUG": "0"}
    assert got.tags == ["test"]
    assert got.allowed_workflows == ["test-wf"]

    decrypts = [
        e for e in audit.emit_calls if e["event_type"] == "secret.decrypted"
    ]
    assert any("vault_request_id" in e["details"] for e in decrypts)

    await b.delete_profile(profile_id)
    with pytest.raises(ProfileNotFoundError):
        await b.get_profile(profile_id)


@pytest.mark.asyncio
async def test_mixed_cascade_with_builtin_and_vault(backend, tmp_path):
    """Cascade resolves correctly with system=builtin, workflow=vault."""
    from cryptography.fernet import Fernet

    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.crypto import Crypto
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.refs import register_backend
    from sagewai.sealed.resolution import (
        CascadeLevel,
        resolve_security_profile,
    )

    b, _ = backend
    register_backend(b)

    builtin = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=Crypto(Fernet.generate_key()),
    )
    register_backend(builtin)
    await builtin.save_profile(
        ProfileWritePayload(
            id="sys-defaults",
            name="System",
            env={"SAGEWAI_TONE": "formal", "DEBUG": "0"},
        )
    )

    await b.save_profile(
        ProfileWritePayload(
            id="cascade-acme",
            name="Cascade Acme",
            env={"DEBUG": "1"},
            secrets={"OPENAI_API_KEY": "sk-cascade"},
        )
    )

    levels = [
        CascadeLevel(name="system", profile_ref="builtin://sys-defaults", overrides=None),
        CascadeLevel(name="workflow", profile_ref="vault://cascade-acme", overrides=None),
    ]
    eff = await resolve_security_profile(levels=levels, audit_writer=None)

    assert eff.env["DEBUG"] == "1"
    assert eff.env["SAGEWAI_TONE"] == "formal"
    assert eff.env["OPENAI_API_KEY"] == "sk-cascade"

    await b.delete_profile("cascade-acme")
