# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Local (Fernet) backend tests."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from sagewai.connections.credentials.errors import BackendUnhealthyError
from sagewai.connections.credentials.local import LocalBackend


@pytest.fixture(autouse=True)
def _seed_master_key(monkeypatch):
    """Every test gets a fresh Fernet master key in env."""
    key = Fernet.generate_key()
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", key.decode())
    yield


def test_identity():
    b = LocalBackend()
    assert b.id == "local"
    assert b.display_name == "Local encrypted file"


def test_encrypt_then_decrypt_roundtrip_top_level():
    b = LocalBackend()
    data = {"client_secret": "real-secret", "client_id": "cid"}
    encrypted = b.encrypt_fields(
        data, sensitive_field_paths=("client_secret",), backend_config={}
    )
    # client_id (not sensitive) is untouched; client_secret is ciphertext.
    assert encrypted["client_id"] == "cid"
    assert encrypted["client_secret"] != "real-secret"
    assert encrypted["client_secret"].startswith("fernet:")  # Crypto.PREFIX
    decrypted = b.decrypt_fields(
        encrypted, sensitive_field_paths=("client_secret",), backend_config={}
    )
    assert decrypted["client_secret"] == "real-secret"


def test_encrypt_then_decrypt_roundtrip_nested():
    b = LocalBackend()
    data = {
        "client_secret": "csec",
        "tokens": {"access_token": "AT", "refresh_token": "RT", "token_type": "Bearer"},
    }
    paths = ("client_secret", "tokens.access_token", "tokens.refresh_token")
    encrypted = b.encrypt_fields(data, sensitive_field_paths=paths, backend_config={})
    # All three sensitive leaves now ciphertext
    assert encrypted["client_secret"].startswith("fernet:")
    assert encrypted["tokens"]["access_token"].startswith("fernet:")
    assert encrypted["tokens"]["refresh_token"].startswith("fernet:")
    # Non-sensitive leaf preserved
    assert encrypted["tokens"]["token_type"] == "Bearer"
    decrypted = b.decrypt_fields(encrypted, sensitive_field_paths=paths, backend_config={})
    assert decrypted["client_secret"] == "csec"
    assert decrypted["tokens"]["access_token"] == "AT"
    assert decrypted["tokens"]["refresh_token"] == "RT"


def test_encrypt_missing_path_is_noop():
    """Encryption only applies to fields that exist (e.g., pending records have no tokens)."""
    b = LocalBackend()
    data = {"client_secret": "csec"}  # no tokens
    encrypted = b.encrypt_fields(
        data,
        sensitive_field_paths=("client_secret", "tokens.access_token"),
        backend_config={},
    )
    assert encrypted["client_secret"].startswith("fernet:")
    assert "tokens" not in encrypted  # no key created


def test_health_ok_when_master_key_set():
    b = LocalBackend()
    result = b.health(backend_config={})
    assert result.ok is True


def test_health_not_ok_when_master_key_missing(monkeypatch):
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    b = LocalBackend()
    result = b.health(backend_config={})
    assert result.ok is False
    assert "master key" in (result.message or "").lower()


def test_validate_config_accepts_empty():
    b = LocalBackend()
    b.validate_config({})  # no raise


def test_validate_config_rejects_extra_keys():
    b = LocalBackend()
    from sagewai.connections.credentials.errors import InvalidBackendConfigError
    with pytest.raises(InvalidBackendConfigError):
        b.validate_config({"key": "x"})  # local takes no config


def test_decrypt_garbage_raises():
    """If a ciphertext can't decrypt (e.g., wrong key), surface as a clear error."""
    b = LocalBackend()
    # The local backend wraps SecretCorrupted from Sealed.Crypto into a clear failure
    bad = {"client_secret": "fernet:not-a-valid-ciphertext"}
    with pytest.raises(BackendUnhealthyError):
        b.decrypt_fields(bad, sensitive_field_paths=("client_secret",), backend_config={})
