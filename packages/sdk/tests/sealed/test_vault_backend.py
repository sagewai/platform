# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for Sealed-ii.Vault backend."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest  # noqa: F401  -- used by later task additions (raises, fixtures, marks)


class TestVaultErrors:
    def test_backend_transport_error_is_exception(self):
        from sagewai.sealed.backend import BackendTransportError
        assert issubclass(BackendTransportError, Exception)

    def test_vault_unreachable_inherits_transport(self):
        from sagewai.sealed.backend import (
            BackendTransportError,
            VaultUnreachableError,
        )
        assert issubclass(VaultUnreachableError, BackendTransportError)

    def test_vault_auth_inherits_transport(self):
        from sagewai.sealed.backend import BackendTransportError, VaultAuthError
        assert issubclass(VaultAuthError, BackendTransportError)

    def test_vault_config_inherits_transport(self):
        from sagewai.sealed.backend import BackendTransportError, VaultConfigError
        assert issubclass(VaultConfigError, BackendTransportError)

    def test_errors_carry_message(self):
        from sagewai.sealed.backend import VaultAuthError
        err = VaultAuthError("login failed")
        assert "login failed" in str(err)


@pytest.fixture
def stub_hvac(monkeypatch):
    """Stubs hvac.Client so VaultBackend can be tested without a Vault server.

    Returns the client mock so tests can assert calls / set return values.
    """
    import hvac
    client = MagicMock(spec=hvac.Client)
    client.is_authenticated.return_value = True
    client.adapter = MagicMock()
    client.secrets = MagicMock()
    client.auth = MagicMock()

    # hvac.Client(...) returns this mock instance
    def _factory(*args, **kwargs):
        client.__init_args__ = (args, kwargs)
        return client

    monkeypatch.setattr("hvac.Client", _factory)
    return client


class TestVaultBackendSkeleton:
    def test_name_and_scheme(self, stub_hvac):
        from sagewai.sealed.vault_backend import VaultBackend
        b = VaultBackend(
            addr="http://test:8200",
            namespace=None,
            auth_method="token",
            auth_config={"token": "test"},
            mount="kv",
        )
        assert b.name == "vault"
        assert b.scheme == "vault"

    def test_token_auth_passes_token_to_hvac_client(self, stub_hvac):
        from sagewai.sealed.vault_backend import VaultBackend
        VaultBackend(
            addr="http://test:8200",
            namespace=None,
            auth_method="token",
            auth_config={"token": "rooty"},
            mount="kv",
        )
        _, kwargs = stub_hvac.__init_args__
        assert kwargs["url"] == "http://test:8200"
        assert kwargs["token"] == "rooty"

    def test_token_auth_reads_from_env_when_token_env_specified(
        self, stub_hvac, monkeypatch,
    ):
        monkeypatch.setenv("VAULT_TOKEN", "from-env")
        from sagewai.sealed.vault_backend import VaultBackend
        VaultBackend(
            addr="http://test:8200",
            namespace=None,
            auth_method="token",
            auth_config={"token_env": "VAULT_TOKEN"},
            mount="kv",
        )
        _, kwargs = stub_hvac.__init_args__
        assert kwargs["token"] == "from-env"

    def test_token_auth_raises_vault_config_error_if_neither_token_nor_env(
        self, stub_hvac,
    ):
        from sagewai.sealed.backend import VaultConfigError
        from sagewai.sealed.vault_backend import VaultBackend
        with pytest.raises(VaultConfigError):
            VaultBackend(
                addr="http://test:8200",
                namespace=None,
                auth_method="token",
                auth_config={},
                mount="kv",
            )

    def test_namespace_passed_to_hvac_client(self, stub_hvac):
        from sagewai.sealed.vault_backend import VaultBackend
        VaultBackend(
            addr="http://test:8200",
            namespace="acme",
            auth_method="token",
            auth_config={"token": "t"},
            mount="kv",
        )
        _, kwargs = stub_hvac.__init_args__
        assert kwargs.get("namespace") == "acme"

    @pytest.mark.asyncio
    async def test_ensure_authenticated_short_circuits_when_already_auth(
        self, stub_hvac,
    ):
        from sagewai.sealed.vault_backend import VaultBackend
        b = VaultBackend(
            addr="http://test:8200",
            namespace=None,
            auth_method="token",
            auth_config={"token": "t"},
            mount="kv",
        )
        stub_hvac.is_authenticated.return_value = True
        await b._ensure_authenticated()
        # Token auth has nothing to re-do; AppRole/k8s would re-login.
        # Assert no exceptions; client unchanged.
        assert stub_hvac.is_authenticated.called

    def test_unsupported_auth_method_raises_vault_config_error(self, stub_hvac):
        from sagewai.sealed.backend import VaultConfigError
        from sagewai.sealed.vault_backend import VaultBackend
        with pytest.raises(VaultConfigError):
            VaultBackend(
                addr="http://test:8200",
                namespace=None,
                auth_method="oauth",  # not supported
                auth_config={},
                mount="kv",
            )


class TestVaultAuthMethods:
    @pytest.mark.asyncio
    async def test_approle_login_calls_hvac_approle_with_role_id_and_secret(
        self, stub_hvac, monkeypatch,
    ):
        monkeypatch.setenv("MY_SECRET_ID", "secret-from-env")
        from sagewai.sealed.vault_backend import VaultBackend
        b = VaultBackend(
            addr="http://test:8200",
            namespace=None,
            auth_method="approle",
            auth_config={"role_id": "role-uuid", "secret_id_env": "MY_SECRET_ID"},
            mount="kv",
        )
        stub_hvac.is_authenticated.return_value = False
        await b._ensure_authenticated()
        stub_hvac.auth.approle.login.assert_called_once_with(
            role_id="role-uuid", secret_id="secret-from-env",
        )

    def test_approle_missing_role_id_raises(self, stub_hvac):
        from sagewai.sealed.backend import VaultConfigError
        from sagewai.sealed.vault_backend import VaultBackend
        with pytest.raises(VaultConfigError):
            VaultBackend(
                addr="http://test:8200",
                namespace=None,
                auth_method="approle",
                auth_config={"secret_id_env": "X"},
                mount="kv",
            )

    def test_approle_missing_secret_id_env_raises(self, stub_hvac):
        from sagewai.sealed.backend import VaultConfigError
        from sagewai.sealed.vault_backend import VaultBackend
        with pytest.raises(VaultConfigError):
            VaultBackend(
                addr="http://test:8200",
                namespace=None,
                auth_method="approle",
                auth_config={"role_id": "x"},
                mount="kv",
            )

    @pytest.mark.asyncio
    async def test_kubernetes_login_reads_jwt_and_calls_hvac_k8s(
        self, stub_hvac, tmp_path,
    ):
        token_path = tmp_path / "sa-token"
        token_path.write_text("k8s-jwt")
        from sagewai.sealed.vault_backend import VaultBackend
        b = VaultBackend(
            addr="http://test:8200",
            namespace=None,
            auth_method="kubernetes",
            auth_config={"role": "sagewai", "token_path": str(token_path)},
            mount="kv",
        )
        stub_hvac.is_authenticated.return_value = False
        await b._ensure_authenticated()
        stub_hvac.auth.kubernetes.login.assert_called_once_with(
            role="sagewai", jwt="k8s-jwt",
        )

    def test_kubernetes_missing_role_raises(self, stub_hvac, tmp_path):
        from sagewai.sealed.backend import VaultConfigError
        from sagewai.sealed.vault_backend import VaultBackend
        with pytest.raises(VaultConfigError):
            VaultBackend(
                addr="http://test:8200",
                namespace=None,
                auth_method="kubernetes",
                auth_config={"token_path": str(tmp_path / "x")},
                mount="kv",
            )

    def test_kubernetes_default_token_path(self, stub_hvac):
        # Constructor must not blow up if token_path is absent — defaults
        # to the standard k8s injected location. Login will read at
        # auth-time, not construct-time.
        from sagewai.sealed.vault_backend import VaultBackend
        b = VaultBackend(
            addr="http://test:8200",
            namespace=None,
            auth_method="kubernetes",
            auth_config={"role": "sagewai"},
            mount="kv",
        )
        assert b._auth_config["role"] == "sagewai"


class TestVaultBackendReads:
    @pytest.fixture
    def backend_with_audit(self, stub_hvac):
        from sagewai.sealed.vault_backend import VaultBackend
        audit = MagicMock()
        audit.emit_calls = []

        async def _emit(**kwargs):
            audit.emit_calls.append(kwargs)

        audit.emit = _emit
        b = VaultBackend(
            addr="http://test:8200",
            namespace=None,
            auth_method="token",
            auth_config={"token": "t"},
            mount="kv",
            audit_writer=audit,
        )
        return b, audit, stub_hvac

    @pytest.mark.asyncio
    async def test_get_profile_returns_full_profile_with_secrets(
        self, backend_with_audit,
    ):
        b, _audit, client = backend_with_audit
        client.secrets.kv.v2.read_secret_version.return_value = {
            "request_id": "req-abc",
            "data": {
                "data": {
                    "name": "Acme Prod",
                    "description": "live",
                    "owner": "ops@acme.com",
                    "tags": ["prod"],
                    "allowed_workflows": ["billing"],
                    "env": {"DEBUG": "0"},
                    "secrets": {"OPENAI_API_KEY": "sk-1", "AWS_KEY": "akia"},
                },
                "metadata": {"created_time": "2026-04-28T10:00:00Z", "version": 3},
            },
        }
        prof = await b.get_profile("sagewai/acme-prod")
        assert prof.id == "sagewai/acme-prod"
        assert prof.name == "Acme Prod"
        assert prof.secrets == {"OPENAI_API_KEY": "sk-1", "AWS_KEY": "akia"}
        assert prof.env == {"DEBUG": "0"}
        assert prof.tags == ["prod"]
        assert prof.allowed_workflows == ["billing"]
        assert sorted(prof.secret_keys) == ["AWS_KEY", "OPENAI_API_KEY"]

    @pytest.mark.asyncio
    async def test_get_profile_emits_one_secret_decrypted_per_key(
        self, backend_with_audit,
    ):
        b, audit, client = backend_with_audit
        client.secrets.kv.v2.read_secret_version.return_value = {
            "request_id": "req-xyz",
            "data": {
                "data": {
                    "name": "x",
                    "env": {},
                    "secrets": {"K1": "v1", "K2": "v2"},
                },
                "metadata": {"created_time": "2026-04-28T10:00:00Z", "version": 1},
            },
        }
        await b.get_profile("p1")
        events = [
            e for e in audit.emit_calls if e["event_type"] == "secret.decrypted"
        ]
        assert len(events) == 2
        assert {e["secret_key"] for e in events} == {"K1", "K2"}
        for e in events:
            assert e["details"]["vault_request_id"] == "req-xyz"

    @pytest.mark.asyncio
    async def test_get_profile_with_capture_disabled_omits_request_id(
        self, stub_hvac,
    ):
        from sagewai.sealed.vault_backend import VaultBackend
        audit = MagicMock()
        audit.emit_calls = []

        async def _emit(**kwargs):
            audit.emit_calls.append(kwargs)

        audit.emit = _emit
        b = VaultBackend(
            addr="http://test:8200",
            namespace=None,
            auth_method="token",
            auth_config={"token": "t"},
            mount="kv",
            audit_writer=audit,
            capture_request_id=False,
        )
        stub_hvac.secrets.kv.v2.read_secret_version.return_value = {
            "request_id": "req-x",
            "data": {
                "data": {"name": "x", "env": {}, "secrets": {"K": "v"}},
                "metadata": {"created_time": "2026-04-28T10:00:00Z", "version": 1},
            },
        }
        await b.get_profile("p1")
        ev = [
            e for e in audit.emit_calls if e["event_type"] == "secret.decrypted"
        ][0]
        assert "vault_request_id" not in ev["details"]

    @pytest.mark.asyncio
    async def test_get_profile_raises_profile_not_found_on_invalid_path(
        self, backend_with_audit,
    ):
        from hvac.exceptions import InvalidPath

        from sagewai.sealed.backend import ProfileNotFoundError
        b, _, client = backend_with_audit
        client.secrets.kv.v2.read_secret_version.side_effect = InvalidPath
        with pytest.raises(ProfileNotFoundError):
            await b.get_profile("missing")

    @pytest.mark.asyncio
    async def test_get_profile_metadata_does_not_emit_secret_decrypted(
        self, backend_with_audit,
    ):
        b, audit, client = backend_with_audit
        client.secrets.kv.v2.read_secret_metadata.return_value = {
            "request_id": "req-m",
            "data": {
                "current_version": 5,
                "created_time": "2026-04-28T11:00:00Z",
                "custom_metadata": {
                    "name": "Acme Prod",
                    "description": "live",
                    "tags": "prod,billable",
                    "env_keys": "DEBUG",
                    "secret_keys": "OPENAI_API_KEY,AWS_KEY",
                },
            },
        }
        meta = await b.get_profile_metadata("sagewai/acme-prod")
        assert meta.id == "sagewai/acme-prod"
        assert sorted(meta.secret_keys) == ["AWS_KEY", "OPENAI_API_KEY"]
        decrypts = [
            e for e in audit.emit_calls if e["event_type"] == "secret.decrypted"
        ]
        assert decrypts == []


class TestVaultBackendWrites:
    @pytest.fixture
    def backend_with_audit(self, stub_hvac):
        from sagewai.sealed.vault_backend import VaultBackend
        audit = MagicMock()
        audit.emit_calls = []

        async def _emit(**kwargs):
            audit.emit_calls.append(kwargs)

        audit.emit = _emit
        b = VaultBackend(
            addr="http://test:8200",
            namespace=None,
            auth_method="token",
            auth_config={"token": "t"},
            mount="kv",
            audit_writer=audit,
        )
        return b, audit, stub_hvac

    @pytest.mark.asyncio
    async def test_save_profile_create_emits_profile_created(
        self, backend_with_audit,
    ):
        from hvac.exceptions import InvalidPath

        from sagewai.sealed.models import ProfileWritePayload
        b, audit, client = backend_with_audit
        client.secrets.kv.v2.read_secret_metadata.side_effect = InvalidPath
        client.secrets.kv.v2.create_or_update_secret.return_value = {
            "request_id": "req-w",
            "data": {"version": 1},
        }
        client.secrets.kv.v2.read_secret_version.return_value = {
            "request_id": "req-r",
            "data": {
                "data": {
                    "name": "Acme",
                    "env": {},
                    "secrets": {"K": "v"},
                },
                "metadata": {"created_time": "2026-04-28T10:00:00Z", "version": 1},
            },
        }
        await b.save_profile(
            ProfileWritePayload(id="acme", name="Acme", secrets={"K": "v"})
        )
        events = [e["event_type"] for e in audit.emit_calls]
        assert "profile.created" in events
        assert "profile.updated" not in events

    @pytest.mark.asyncio
    async def test_save_profile_update_emits_profile_updated(
        self, backend_with_audit,
    ):
        from sagewai.sealed.models import ProfileWritePayload
        b, audit, client = backend_with_audit
        client.secrets.kv.v2.read_secret_metadata.return_value = {
            "data": {"current_version": 1, "custom_metadata": {}},
        }
        client.secrets.kv.v2.create_or_update_secret.return_value = {
            "data": {"version": 2}, "request_id": "req-w",
        }
        client.secrets.kv.v2.read_secret_version.return_value = {
            "request_id": "req-r",
            "data": {
                "data": {"name": "Acme v2", "env": {}, "secrets": {}},
                "metadata": {"created_time": "2026-04-28T10:00:00Z", "version": 2},
            },
        }
        await b.save_profile(ProfileWritePayload(id="acme", name="Acme v2"))
        events = [e["event_type"] for e in audit.emit_calls]
        assert "profile.updated" in events
        assert "profile.created" not in events

    @pytest.mark.asyncio
    async def test_save_profile_writes_custom_metadata(self, backend_with_audit):
        from hvac.exceptions import InvalidPath

        from sagewai.sealed.models import ProfileWritePayload
        b, _, client = backend_with_audit
        client.secrets.kv.v2.read_secret_metadata.side_effect = InvalidPath
        client.secrets.kv.v2.create_or_update_secret.return_value = {
            "data": {"version": 1}
        }
        client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {
                "data": {"name": "x", "env": {}, "secrets": {}},
                "metadata": {"created_time": "2026-04-28T10:00:00Z"},
            },
        }
        await b.save_profile(
            ProfileWritePayload(
                id="acme",
                name="Acme",
                tags=["prod", "billing"],
                env={"DEBUG": "0"},
                secrets={"K": "v"},
                allowed_workflows=["wf"],
            )
        )
        client.secrets.kv.v2.update_metadata.assert_called_once()
        kwargs = client.secrets.kv.v2.update_metadata.call_args.kwargs
        custom = kwargs["custom_metadata"]
        assert custom["name"] == "Acme"
        assert "prod" in custom["tags"]
        assert "K" in custom["secret_keys"]
        assert "DEBUG" in custom["env_keys"]

    @pytest.mark.asyncio
    async def test_save_profile_requires_id(self, backend_with_audit):
        from sagewai.sealed.models import ProfileWritePayload
        b, _, _ = backend_with_audit
        with pytest.raises(ValueError):
            await b.save_profile(ProfileWritePayload(id=None, name="x"))

    @pytest.mark.asyncio
    async def test_delete_profile_destroys_and_emits(self, backend_with_audit):
        b, audit, client = backend_with_audit
        client.secrets.kv.v2.read_secret_metadata.return_value = {"data": {}}
        await b.delete_profile("acme")
        client.secrets.kv.v2.delete_metadata_and_all_versions.assert_called_once_with(
            path="acme", mount_point="kv",
        )
        events = [e["event_type"] for e in audit.emit_calls]
        assert "profile.deleted" in events

    @pytest.mark.asyncio
    async def test_delete_profile_raises_not_found_on_invalid_path(
        self, backend_with_audit,
    ):
        from hvac.exceptions import InvalidPath

        from sagewai.sealed.backend import ProfileNotFoundError
        b, _, client = backend_with_audit
        client.secrets.kv.v2.read_secret_metadata.side_effect = InvalidPath
        with pytest.raises(ProfileNotFoundError):
            await b.delete_profile("missing")

    @pytest.mark.asyncio
    async def test_list_profiles_returns_metadata(self, backend_with_audit):
        b, _, client = backend_with_audit
        client.secrets.kv.v2.list_secrets.return_value = {
            "data": {"keys": ["acme", "billing", "report-gen"]},
        }
        client.secrets.kv.v2.read_secret_metadata.return_value = {
            "data": {
                "current_version": 1,
                "created_time": "2026-04-28T10:00:00Z",
                "custom_metadata": {
                    "name": "X",
                    "secret_keys": "K",
                    "env_keys": "",
                    "tags": "",
                },
            },
        }
        metas = await b.list_profiles()
        assert len(metas) == 3
        assert {m.id for m in metas} == {"acme", "billing", "report-gen"}

    @pytest.mark.asyncio
    async def test_list_profiles_empty_when_no_keys(self, backend_with_audit):
        from hvac.exceptions import InvalidPath
        b, _, client = backend_with_audit
        client.secrets.kv.v2.list_secrets.side_effect = InvalidPath
        metas = await b.list_profiles()
        assert metas == []


class TestVaultBackendMasterKey:
    @pytest.mark.asyncio
    async def test_supports_master_key_rotation_returns_false(self, stub_hvac):
        from sagewai.sealed.vault_backend import VaultBackend
        b = VaultBackend(
            addr="http://t", namespace=None, auth_method="token",
            auth_config={"token": "t"}, mount="kv",
        )
        assert await b.supports_master_key_rotation() is False

    @pytest.mark.asyncio
    async def test_rotate_master_key_raises_unsupported(self, stub_hvac):
        from sagewai.sealed.backend import BackendUnsupportedOperationError
        from sagewai.sealed.vault_backend import VaultBackend
        b = VaultBackend(
            addr="http://t", namespace=None, auth_method="token",
            auth_config={"token": "t"}, mount="kv",
        )
        with pytest.raises(BackendUnsupportedOperationError) as exc_info:
            await b.rotate_master_key(b"new-key")
        assert "Vault" in str(exc_info.value)


class TestVaultBackendErrors:
    @pytest.fixture
    def backend(self, stub_hvac):
        from sagewai.sealed.vault_backend import VaultBackend
        return VaultBackend(
            addr="http://t", namespace=None, auth_method="token",
            auth_config={"token": "t"}, mount="kv",
        ), stub_hvac

    @pytest.mark.asyncio
    async def test_forbidden_translates_to_vault_auth_error(self, backend):
        from hvac.exceptions import Forbidden

        from sagewai.sealed.backend import VaultAuthError
        b, client = backend
        client.secrets.kv.v2.read_secret_version.side_effect = Forbidden(
            "permission denied"
        )
        with pytest.raises(VaultAuthError):
            await b.get_profile("p1")

    @pytest.mark.asyncio
    async def test_vault_down_translates_to_vault_unreachable(self, backend):
        from hvac.exceptions import VaultDown

        from sagewai.sealed.backend import VaultUnreachableError
        b, client = backend
        client.secrets.kv.v2.read_secret_version.side_effect = VaultDown(
            "5xx", errors=["sealed"]
        )
        with pytest.raises(VaultUnreachableError):
            await b.get_profile("p1")

    @pytest.mark.asyncio
    async def test_connection_error_translates_to_vault_unreachable(self, backend):
        from sagewai.sealed.backend import VaultUnreachableError
        b, client = backend
        client.secrets.kv.v2.read_secret_version.side_effect = ConnectionError(
            "refused"
        )
        with pytest.raises(VaultUnreachableError):
            await b.get_profile("p1")


class TestVaultBackendFactory:
    def test_factory_returns_none_when_disabled(self, stub_hvac):
        from sagewai.sealed.vault_backend import build_vault_backend_from_config
        cfg = {"enabled": False, "addr": "http://t"}
        assert build_vault_backend_from_config(cfg) is None

    def test_factory_returns_none_on_empty_config(self, stub_hvac):
        from sagewai.sealed.vault_backend import build_vault_backend_from_config
        assert build_vault_backend_from_config({}) is None

    def test_factory_returns_backend_when_enabled(self, stub_hvac):
        from sagewai.sealed.vault_backend import (
            VaultBackend,
            build_vault_backend_from_config,
        )
        cfg = {
            "enabled": True,
            "addr": "http://t:8200",
            "auth_method": "token",
            "auth_config": {"token": "t"},
            "mount": "kv",
        }
        b = build_vault_backend_from_config(cfg)
        assert isinstance(b, VaultBackend)

    def test_factory_raises_vault_config_error_on_missing_addr(self, stub_hvac):
        from sagewai.sealed.backend import VaultConfigError
        from sagewai.sealed.vault_backend import build_vault_backend_from_config
        with pytest.raises(VaultConfigError):
            build_vault_backend_from_config(
                {"enabled": True, "auth_method": "token", "auth_config": {"token": "t"}}
            )

    @pytest.mark.asyncio
    async def test_lifecycle_emit_startup_authenticated(self, stub_hvac):
        from sagewai.sealed._vault_lifecycle import emit_startup_authenticated
        audit = MagicMock()
        audit.emit_calls = []

        async def _emit(**kwargs):
            audit.emit_calls.append(kwargs)

        audit.emit = _emit

        await emit_startup_authenticated(
            audit_writer=audit,
            addr="http://t",
            namespace=None,
            auth_method="token",
            request_id="req-startup",
        )
        assert len(audit.emit_calls) == 1
        ev = audit.emit_calls[0]
        assert ev["event_type"] == "vault.startup.authenticated"
        assert ev["details"]["addr"] == "http://t"
        assert ev["details"]["auth_method"] == "token"
        assert ev["details"]["vault_request_id"] == "req-startup"
