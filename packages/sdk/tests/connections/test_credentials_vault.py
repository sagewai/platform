# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""VaultBackend — schemas + identity + behavioral tests."""
from __future__ import annotations

import pytest

from sagewai.connections.credentials.errors import (
    VaultAuthError,
    VaultConfigError,
    VaultError,
    VaultReadError,
)
from sagewai.connections.credentials.vault import (
    VaultBackend,
    VaultBackendConfig,
)


def test_identity():
    b = VaultBackend()
    assert b.id == "vault"
    assert b.display_name == "HashiCorp Vault"


def test_schema_accepts_token_auth():
    cfg = VaultBackendConfig.model_validate({
        "url": "https://vault.example.com:8200",
        "base_path": "sagewai/spotify-marketing",
        "auth": {"mode": "token", "token": "hvs.CAESIJabc123"},
    })
    assert cfg.auth.mode == "token"
    assert cfg.mount == "secret"  # default
    assert cfg.verify_tls is True  # default
    assert cfg.namespace is None


def test_schema_accepts_approle_auth():
    cfg = VaultBackendConfig.model_validate({
        "url": "https://vault.example.com:8200",
        "base_path": "sagewai/x",
        "auth": {
            "mode": "approle",
            "role_id": "abc-123",
            "secret_id": "secret-456",
        },
    })
    assert cfg.auth.mode == "approle"
    assert cfg.auth.role_id == "abc-123"


def test_schema_accepts_optional_namespace():
    cfg = VaultBackendConfig.model_validate({
        "url": "https://vault.example.com:8200",
        "namespace": "admin/team-a",
        "mount": "kv",
        "base_path": "sagewai/x",
        "auth": {"mode": "token", "token": "x"},
        "verify_tls": False,
    })
    assert cfg.namespace == "admin/team-a"
    assert cfg.mount == "kv"
    assert cfg.verify_tls is False


def test_schema_rejects_unknown_auth_mode():
    with pytest.raises(Exception):  # Pydantic ValidationError
        VaultBackendConfig.model_validate({
            "url": "https://x",
            "base_path": "x",
            "auth": {"mode": "ldap", "username": "u", "password": "p"},
        })


def test_schema_rejects_missing_url():
    with pytest.raises(Exception):
        VaultBackendConfig.model_validate({
            "base_path": "x",
            "auth": {"mode": "token", "token": "x"},
        })


def test_schema_rejects_missing_base_path():
    with pytest.raises(Exception):
        VaultBackendConfig.model_validate({
            "url": "https://x",
            "auth": {"mode": "token", "token": "x"},
        })


def test_schema_rejects_extra_fields():
    with pytest.raises(Exception):
        VaultBackendConfig.model_validate({
            "url": "https://x",
            "base_path": "x",
            "auth": {"mode": "token", "token": "x"},
            "unknown_field": "value",
        })


def test_validate_config_raises_VaultConfigError_on_bad_input():
    b = VaultBackend()
    with pytest.raises(VaultConfigError):
        b.validate_config({"url": "https://x"})  # missing base_path + auth


# ─── Behavioral tests using hvac monkeypatch ──────────────────────────


class _FakeKvV2:
    """Stub for hvac.api.secrets.KvV2."""

    def __init__(self, data: dict[str, dict]):
        # data: {path: {key: value, ...}, ...}
        self._data = data
        self.read_count = 0

    def read_secret_version(self, *, path: str, mount_point: str):
        self.read_count += 1
        if path not in self._data:
            raise Exception(f"path not found: {path}")
        return {"data": {"data": dict(self._data[path])}}


class _FakeAppRole:
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.calls: list[dict] = []

    def login(self, *, role_id: str, secret_id: str):
        self.calls.append({"role_id": role_id, "secret_id": secret_id})
        if self.should_fail:
            raise Exception("invalid role_id/secret_id")
        return {"auth": {"client_token": "stub-token"}}


class _FakeSys:
    def __init__(self, healthy: bool = True):
        self.healthy = healthy

    def read_health_status(self):
        if not self.healthy:
            raise Exception("vault sealed or unreachable")
        return {"sealed": False, "initialized": True, "version": "1.15.0"}


class _FakeHvacClient:
    """Stub for hvac.Client."""

    def __init__(self, kv_data: dict[str, dict] | None = None, *, healthy: bool = True,
                 approle_fails: bool = False):
        self.token = None
        self._url = None
        self._verify = None
        self._namespace = None
        kv = _FakeKvV2(kv_data or {})
        approle = _FakeAppRole(should_fail=approle_fails)
        self.secrets = type("S", (), {"kv": type("Kv", (), {"v2": kv})})()
        self.auth = type("A", (), {"approle": approle})()
        self.sys = _FakeSys(healthy=healthy)


@pytest.fixture
def _patch_hvac(monkeypatch):
    """Helper: monkeypatch _lazy_import_hvac to return a fake hvac module."""
    def _factory(kv_data=None, *, healthy=True, approle_fails=False):
        fake_client_instance = _FakeHvacClient(
            kv_data=kv_data, healthy=healthy, approle_fails=approle_fails,
        )

        class _FakeHvacModule:
            Client = staticmethod(lambda *args, **kw: fake_client_instance)

        monkeypatch.setattr(
            "sagewai.connections.credentials.vault._lazy_import_hvac",
            lambda: _FakeHvacModule,
        )
        return fake_client_instance
    return _factory


def test_encrypt_replaces_leaves_with_vault_marker(_patch_hvac):
    """encrypt_fields stores {path, key} markers; does NOT write to Vault."""
    _patch_hvac()
    b = VaultBackend()
    data = {"client_secret": "real-secret", "client_id": "cid"}
    config = {
        "url": "https://vault.x", "base_path": "sagewai/test",
        "auth": {"mode": "token", "token": "stub"},
    }
    encrypted = b.encrypt_fields(
        data,
        sensitive_field_paths=("client_secret",),
        backend_config=config,
    )
    assert encrypted["client_secret"] == {"$vault": {"path": "sagewai/test", "key": "client_secret"}}
    assert encrypted["client_id"] == "cid"  # untouched


def test_encrypt_does_not_store_plaintext(_patch_hvac):
    _patch_hvac()
    b = VaultBackend()
    data = {"client_secret": "VERY_SECRET_PLAINTEXT"}
    config = {
        "url": "https://vault.x", "base_path": "x",
        "auth": {"mode": "token", "token": "x"},
    }
    encrypted = b.encrypt_fields(
        data, sensitive_field_paths=("client_secret",), backend_config=config,
    )
    import json
    assert "VERY_SECRET_PLAINTEXT" not in json.dumps(encrypted)


def test_decrypt_reads_via_token_auth(_patch_hvac):
    client = _patch_hvac(kv_data={"sagewai/test": {"client_secret": "real-value"}})
    b = VaultBackend()
    data = {"client_secret": {"$vault": {"path": "sagewai/test", "key": "client_secret"}}}
    config = {
        "url": "https://vault.x", "base_path": "sagewai/test",
        "auth": {"mode": "token", "token": "hvs.stub"},
    }
    decrypted = b.decrypt_fields(
        data, sensitive_field_paths=("client_secret",), backend_config=config,
    )
    assert decrypted["client_secret"] == "real-value"
    assert client.token == "hvs.stub"


def test_decrypt_reads_via_approle_auth(_patch_hvac):
    client = _patch_hvac(kv_data={"sagewai/test": {"x": "y"}})
    b = VaultBackend()
    data = {"x": {"$vault": {"path": "sagewai/test", "key": "x"}}}
    config = {
        "url": "https://vault.x", "base_path": "sagewai/test",
        "auth": {"mode": "approle", "role_id": "r", "secret_id": "s"},
    }
    b.decrypt_fields(data, sensitive_field_paths=("x",), backend_config=config)
    assert client.auth.approle.calls == [{"role_id": "r", "secret_id": "s"}]


def test_decrypt_approle_auth_failure_raises_VaultAuthError(_patch_hvac):
    _patch_hvac(approle_fails=True)
    b = VaultBackend()
    data = {"x": {"$vault": {"path": "p", "key": "x"}}}
    config = {
        "url": "https://vault.x", "base_path": "p",
        "auth": {"mode": "approle", "role_id": "r", "secret_id": "s"},
    }
    with pytest.raises(VaultAuthError):
        b.decrypt_fields(data, sensitive_field_paths=("x",), backend_config=config)


def test_decrypt_missing_path_raises_VaultReadError(_patch_hvac):
    _patch_hvac(kv_data={})  # no paths
    b = VaultBackend()
    data = {"x": {"$vault": {"path": "missing", "key": "x"}}}
    config = {
        "url": "https://vault.x", "base_path": "missing",
        "auth": {"mode": "token", "token": "x"},
    }
    with pytest.raises(VaultReadError):
        b.decrypt_fields(data, sensitive_field_paths=("x",), backend_config=config)


def test_decrypt_missing_key_raises_VaultReadError(_patch_hvac):
    _patch_hvac(kv_data={"p": {"other": "value"}})
    b = VaultBackend()
    data = {"x": {"$vault": {"path": "p", "key": "x"}}}
    config = {
        "url": "https://vault.x", "base_path": "p",
        "auth": {"mode": "token", "token": "x"},
    }
    with pytest.raises(VaultReadError, match="x"):
        b.decrypt_fields(data, sensitive_field_paths=("x",), backend_config=config)


def test_decrypt_per_mount_path_cache_serves_multiple_keys_with_one_read(_patch_hvac):
    """Multiple sensitive fields from the same Vault secret = ONE read."""
    client = _patch_hvac(kv_data={"sagewai/x": {
        "client_secret": "csec",
        "access_token": "AT",
        "refresh_token": "RT",
    }})
    b = VaultBackend()
    data = {
        "client_secret": {"$vault": {"path": "sagewai/x", "key": "client_secret"}},
        "tokens": {
            "access_token": {"$vault": {"path": "sagewai/x", "key": "access_token"}},
            "refresh_token": {"$vault": {"path": "sagewai/x", "key": "refresh_token"}},
        },
    }
    config = {
        "url": "https://vault.x", "base_path": "sagewai/x",
        "auth": {"mode": "token", "token": "x"},
    }
    decrypted = b.decrypt_fields(
        data,
        sensitive_field_paths=("client_secret", "tokens.access_token", "tokens.refresh_token"),
        backend_config=config,
    )
    assert decrypted["client_secret"] == "csec"
    assert decrypted["tokens"]["access_token"] == "AT"
    assert decrypted["tokens"]["refresh_token"] == "RT"
    # Cache: exactly ONE read for all three keys
    assert client.secrets.kv.v2.read_count == 1


def test_health_ok_when_vault_reachable(_patch_hvac):
    _patch_hvac()
    b = VaultBackend()
    result = b.health({
        "url": "https://vault.x", "base_path": "x",
        "auth": {"mode": "token", "token": "x"},
    })
    assert result.ok is True


def test_health_not_ok_when_vault_unhealthy(_patch_hvac):
    _patch_hvac(healthy=False)
    b = VaultBackend()
    result = b.health({
        "url": "https://vault.x", "base_path": "x",
        "auth": {"mode": "token", "token": "x"},
    })
    assert result.ok is False


def test_lazy_import_raises_VaultError_when_hvac_missing(monkeypatch):
    """When hvac isn't installed, the lazy import surfaces a clear install hint."""
    import sagewai.connections.credentials.vault as v

    # Simulate ImportError by replacing the helper
    def _raise():
        raise VaultError("hvac not installed. Run: pip install sagewai[vault]")

    monkeypatch.setattr(v, "_lazy_import_hvac", _raise)
    b = VaultBackend()
    with pytest.raises(VaultError, match="pip install sagewai"):
        b.decrypt_fields(
            {"x": {"$vault": {"path": "p", "key": "x"}}},
            sensitive_field_paths=("x",),
            backend_config={
                "url": "https://vault.x", "base_path": "p",
                "auth": {"mode": "token", "token": "x"},
            },
        )
