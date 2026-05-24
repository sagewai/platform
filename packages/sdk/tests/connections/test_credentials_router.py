# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""CredentialsBackendRouter tests."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from sagewai.connections.credentials.errors import (
    MissingEnvVarError,
    UnknownBackendError,
)
from sagewai.connections.credentials.router import CredentialsBackendRouter


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    yield


def test_default_backend_is_local():
    r = CredentialsBackendRouter()
    backend, config = r.get_backend_for(None)
    assert backend.id == "local"
    assert config == {}


def test_per_connection_backend_overrides_default():
    r = CredentialsBackendRouter()  # default=local
    backend, config = r.get_backend_for({
        "kind": "env",
        "config": {"field_to_env": {"client_secret": "X"}},
    })
    assert backend.id == "env"
    assert config == {"field_to_env": {"client_secret": "X"}}


def test_unknown_backend_kind_raises():
    r = CredentialsBackendRouter()
    with pytest.raises(UnknownBackendError):
        r.get_backend_for({"kind": "no-such-backend", "config": {}})


def test_encrypt_routes_to_local_by_default():
    r = CredentialsBackendRouter()
    encrypted = r.encrypt(
        {"client_secret": "csec"},
        sensitive_field_paths=("client_secret",),
        connection_credentials_backend=None,
    )
    assert encrypted["client_secret"].startswith("fernet:")


def test_encrypt_routes_to_env_per_connection():
    r = CredentialsBackendRouter()
    encrypted = r.encrypt(
        {"client_secret": "csec"},
        sensitive_field_paths=("client_secret",),
        connection_credentials_backend={
            "kind": "env",
            "config": {"field_to_env": {"client_secret": "SAGEWAI_X"}},
        },
    )
    assert encrypted["client_secret"] == {"$env": "SAGEWAI_X"}


def test_decrypt_routes_per_connection(monkeypatch):
    monkeypatch.setenv("SAGEWAI_X", "real-value")
    r = CredentialsBackendRouter()
    decrypted = r.decrypt(
        {"client_secret": {"$env": "SAGEWAI_X"}},
        sensitive_field_paths=("client_secret",),
        connection_credentials_backend={
            "kind": "env",
            "config": {"field_to_env": {"client_secret": "SAGEWAI_X"}},
        },
    )
    assert decrypted["client_secret"] == "real-value"


def test_swap_local_to_env_re_encrypts(monkeypatch):
    """Swap from local (Fernet) to env (marker). Round-trip preserves plaintext."""
    monkeypatch.setenv("SAGEWAI_NEW_VAR", "the-plaintext-value")
    r = CredentialsBackendRouter()
    # Start: encrypted with local
    local_encrypted = r.encrypt(
        {"client_secret": "the-plaintext-value"},
        sensitive_field_paths=("client_secret",),
        connection_credentials_backend=None,  # local
    )
    assert local_encrypted["client_secret"].startswith("fernet:")
    # Swap to env
    new_data = r.swap(
        local_encrypted,
        sensitive_field_paths=("client_secret",),
        old_credentials_backend=None,
        new_credentials_backend={
            "kind": "env",
            "config": {"field_to_env": {"client_secret": "SAGEWAI_NEW_VAR"}},
        },
    )
    # After swap: env marker, not plaintext, not ciphertext
    assert new_data["client_secret"] == {"$env": "SAGEWAI_NEW_VAR"}


def test_swap_env_to_local_re_encrypts(monkeypatch):
    """Swap from env to local: decrypt-env then encrypt-local."""
    monkeypatch.setenv("SAGEWAI_X", "secret-value")
    r = CredentialsBackendRouter()
    env_data = {"client_secret": {"$env": "SAGEWAI_X"}}
    new_data = r.swap(
        env_data,
        sensitive_field_paths=("client_secret",),
        old_credentials_backend={
            "kind": "env",
            "config": {"field_to_env": {"client_secret": "SAGEWAI_X"}},
        },
        new_credentials_backend=None,  # local
    )
    assert new_data["client_secret"].startswith("fernet:")


def test_swap_propagates_missing_env_var_on_old_backend(monkeypatch):
    """Swap from env to local fails clearly if the source env var is unset."""
    monkeypatch.delenv("SAGEWAI_MISSING", raising=False)
    r = CredentialsBackendRouter()
    env_data = {"client_secret": {"$env": "SAGEWAI_MISSING"}}
    with pytest.raises(MissingEnvVarError):
        r.swap(
            env_data,
            sensitive_field_paths=("client_secret",),
            old_credentials_backend={
                "kind": "env",
                "config": {"field_to_env": {"client_secret": "SAGEWAI_MISSING"}},
            },
            new_credentials_backend=None,
        )


def test_health_routes_per_connection():
    r = CredentialsBackendRouter()
    result = r.health(None)  # local
    assert result.ok is True


def test_constructor_accepts_alternate_default():
    """default_backend constructor param changes platform default."""
    r = CredentialsBackendRouter(default_backend="env")
    backend, _ = r.get_backend_for(None)
    assert backend.id == "env"


def test_constructor_rejects_unknown_default():
    with pytest.raises(UnknownBackendError):
        CredentialsBackendRouter(default_backend="not-a-backend")
