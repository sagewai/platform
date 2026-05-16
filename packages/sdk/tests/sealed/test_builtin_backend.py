# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for BuiltinAdminStoreBackend (Fernet-encrypted JSON file)."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

from sagewai.sealed.audit import AuditWriter
from sagewai.sealed.backend import ProfileNotFoundError
from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
from sagewai.sealed.crypto import Crypto
from sagewai.sealed.models import ProfileWritePayload


@pytest.fixture
def fake_audit():
    fake_store = MagicMock()
    fake_store._pool = MagicMock()
    fake_store._pool.execute = AsyncMock()
    return AuditWriter(fake_store)


@pytest.fixture
def backend(tmp_path, fake_audit):
    key = Fernet.generate_key()
    crypto = Crypto(key)
    return BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=crypto,
        audit_writer=fake_audit,
    )


@pytest.mark.asyncio
async def test_list_empty_at_init(backend):
    assert await backend.list_profiles() == []


@pytest.mark.asyncio
async def test_save_then_get_round_trip(backend):
    payload = ProfileWritePayload(
        id="acme",
        name="Acme Production",
        description="Test",
        secrets={"OPENAI_API_KEY": "sk-secret-value"},
        env={"DEBUG": "1"},
    )
    saved = await backend.save_profile(payload)
    assert saved.id == "acme"
    assert saved.secrets["OPENAI_API_KEY"] == "sk-secret-value"

    loaded = await backend.get_profile("acme")
    assert loaded.secrets["OPENAI_API_KEY"] == "sk-secret-value"
    assert loaded.env["DEBUG"] == "1"


@pytest.mark.asyncio
async def test_secrets_encrypted_at_rest(backend, tmp_path):
    payload = ProfileWritePayload(
        id="acme",
        name="Acme",
        secrets={"OPENAI_API_KEY": "sk-very-secret"},
    )
    await backend.save_profile(payload)

    raw = (tmp_path / "profiles.json").read_text()
    assert "sk-very-secret" not in raw  # plaintext value never on disk
    assert "fernet:" in raw              # ciphertext present


@pytest.mark.asyncio
async def test_metadata_endpoint_excludes_secret_values(backend):
    payload = ProfileWritePayload(
        id="acme",
        name="Acme",
        secrets={"K": "v"},
        env={"E": "x"},
    )
    await backend.save_profile(payload)

    md = await backend.get_profile_metadata("acme")
    assert "K" in md.secret_keys
    assert md.env == {"E": "x"}
    # No 'secrets' attribute on ProfileMetadata
    assert not hasattr(md, "secrets")


@pytest.mark.asyncio
async def test_get_unknown_raises_not_found(backend):
    with pytest.raises(ProfileNotFoundError):
        await backend.get_profile("ghost")


@pytest.mark.asyncio
async def test_delete_round_trip(backend):
    await backend.save_profile(ProfileWritePayload(id="acme", name="Acme"))
    await backend.delete_profile("acme")
    with pytest.raises(ProfileNotFoundError):
        await backend.get_profile("acme")


@pytest.mark.asyncio
async def test_concurrent_save_serialized(backend):
    """Two concurrent saves don't corrupt the file."""
    p1 = ProfileWritePayload(id="a", name="A")
    p2 = ProfileWritePayload(id="b", name="B")
    await asyncio.gather(backend.save_profile(p1), backend.save_profile(p2))
    profiles = await backend.list_profiles()
    ids = {p.id for p in profiles}
    assert ids == {"a", "b"}


@pytest.mark.asyncio
async def test_save_emits_created_audit(backend, fake_audit):
    fake_audit._store._pool.execute.reset_mock()
    await backend.save_profile(ProfileWritePayload(id="acme", name="A"))
    # First call = INSERT for profile.created
    assert fake_audit._store._pool.execute.await_count >= 1
    args = fake_audit._store._pool.execute.await_args_list[0].args
    assert args[1] == "profile.created"


@pytest.mark.asyncio
async def test_get_emits_per_secret_decrypt_audit(backend, fake_audit):
    await backend.save_profile(
        ProfileWritePayload(id="acme", name="A", secrets={"K1": "v1", "K2": "v2"})
    )
    fake_audit._store._pool.execute.reset_mock()
    await backend.get_profile("acme")
    decrypt_calls = [
        c for c in fake_audit._store._pool.execute.await_args_list
        if c.args[1] == "secret.decrypted"
    ]
    assert len(decrypt_calls) == 2


@pytest.mark.asyncio
async def test_rotate_master_key_reencrypts_secrets(tmp_path, fake_audit):
    """rotate_master_key re-encrypts every secret under the new primary.

    After rotation:
      - Returns the count of secret values (not profiles).
      - Subsequent get_profile calls succeed under the new key.
      - The on-disk ciphertext can no longer be decrypted by the old key alone.
    """
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()
    crypto = Crypto(key1)
    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=crypto,
        audit_writer=fake_audit,
    )
    await backend.save_profile(
        ProfileWritePayload(id="acme", name="A", secrets={"K1": "v1", "K2": "v2"})
    )
    await backend.save_profile(
        ProfileWritePayload(id="solo", name="S", secrets={"K3": "v3"})
    )
    # Profile with no secrets — contributes 0 to the count.
    await backend.save_profile(ProfileWritePayload(id="bare", name="B"))

    count = await backend.rotate_master_key(key2)
    assert count == 3  # 2 + 1 + 0 secret values across all three profiles

    # Secrets still decrypt correctly under the new key.
    acme = await backend.get_profile("acme")
    assert acme.secrets == {"K1": "v1", "K2": "v2"}
    solo = await backend.get_profile("solo")
    assert solo.secrets == {"K3": "v3"}

    # On-disk ciphertext cannot be decrypted by the old key alone.
    from sagewai.sealed.crypto import SecretCorrupted
    raw = json.loads((tmp_path / "profiles.json").read_text())
    old_only = Crypto(key1)
    acme_dict = next(p for p in raw["profiles"] if p["id"] == "acme")
    with pytest.raises(SecretCorrupted):
        old_only.decrypt(acme_dict["secrets"]["K1"])
