# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Env-var backend tests."""
from __future__ import annotations

import pytest

from sagewai.connections.credentials.env import EnvBackend
from sagewai.connections.credentials.errors import (
    InvalidBackendConfigError,
    MissingEnvVarError,
)


def test_identity():
    b = EnvBackend()
    assert b.id == "env"
    assert b.display_name == "Environment variables"


def test_encrypt_replaces_leaves_with_env_marker():
    b = EnvBackend()
    data = {"client_secret": "real-secret", "client_id": "cid"}
    config = {"field_to_env": {"client_secret": "SAGEWAI_TEST_SECRET"}}
    encrypted = b.encrypt_fields(
        data, sensitive_field_paths=("client_secret",), backend_config=config
    )
    assert encrypted["client_secret"] == {"$env": "SAGEWAI_TEST_SECRET"}
    assert encrypted["client_id"] == "cid"  # untouched


def test_encrypt_does_not_store_plaintext_anywhere():
    """The plaintext value must NOT appear in the output dict (or any subdict)."""
    b = EnvBackend()
    data = {"client_secret": "VERY_SECRET_PLAINTEXT"}
    config = {"field_to_env": {"client_secret": "SAGEWAI_X"}}
    encrypted = b.encrypt_fields(
        data, sensitive_field_paths=("client_secret",), backend_config=config
    )
    import json
    assert "VERY_SECRET_PLAINTEXT" not in json.dumps(encrypted)


def test_decrypt_reads_env_var(monkeypatch):
    b = EnvBackend()
    monkeypatch.setenv("SAGEWAI_TEST_SECRET", "the-real-secret")
    data = {"client_secret": {"$env": "SAGEWAI_TEST_SECRET"}}
    config = {"field_to_env": {"client_secret": "SAGEWAI_TEST_SECRET"}}
    decrypted = b.decrypt_fields(
        data, sensitive_field_paths=("client_secret",), backend_config=config
    )
    assert decrypted["client_secret"] == "the-real-secret"


def test_decrypt_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("SAGEWAI_TEST_SECRET", raising=False)
    b = EnvBackend()
    data = {"client_secret": {"$env": "SAGEWAI_TEST_SECRET"}}
    config = {"field_to_env": {"client_secret": "SAGEWAI_TEST_SECRET"}}
    with pytest.raises(MissingEnvVarError):
        b.decrypt_fields(
            data, sensitive_field_paths=("client_secret",), backend_config=config
        )


def test_decrypt_skips_paths_without_env_marker():
    """If a leaf is plaintext (operator hasn't switched to env yet), pass through."""
    b = EnvBackend()
    data = {"client_secret": "plain-text-value", "client_id": "cid"}
    config = {"field_to_env": {"client_secret": "SAGEWAI_X"}}
    decrypted = b.decrypt_fields(
        data, sensitive_field_paths=("client_secret",), backend_config=config
    )
    assert decrypted["client_secret"] == "plain-text-value"


def test_health_ok_when_all_vars_set(monkeypatch):
    monkeypatch.setenv("SAGEWAI_A", "1")
    monkeypatch.setenv("SAGEWAI_B", "2")
    b = EnvBackend()
    result = b.health(backend_config={"field_to_env": {"a": "SAGEWAI_A", "b": "SAGEWAI_B"}})
    assert result.ok is True


def test_health_not_ok_when_any_var_missing(monkeypatch):
    monkeypatch.setenv("SAGEWAI_A", "1")
    monkeypatch.delenv("SAGEWAI_MISSING", raising=False)
    b = EnvBackend()
    result = b.health(backend_config={"field_to_env": {"a": "SAGEWAI_A", "b": "SAGEWAI_MISSING"}})
    assert result.ok is False
    assert "SAGEWAI_MISSING" in (result.message or "")


def test_validate_config_requires_field_to_env_dict():
    b = EnvBackend()
    with pytest.raises(InvalidBackendConfigError):
        b.validate_config({})  # missing field_to_env
    with pytest.raises(InvalidBackendConfigError):
        b.validate_config({"field_to_env": "not-a-dict"})


def test_validate_config_requires_str_keys_and_values():
    b = EnvBackend()
    with pytest.raises(InvalidBackendConfigError):
        b.validate_config({"field_to_env": {"x": 123}})  # value not str
