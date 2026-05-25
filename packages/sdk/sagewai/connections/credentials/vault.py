# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""HashiCorp Vault credentials backend.

Stores ``{"$vault": {"path": "...", "key": "..."}}`` markers in place
of sensitive leaves. Decryption reads from Vault KV v2 via the optional
``hvac`` extra (``pip install sagewai[vault]``). Per-call client
construction; no connection pooling. Per-(mount, path) cache within a
single ``decrypt_fields`` call so multiple sensitive fields from the
same Vault secret share one read.

Read-only: the platform never writes to Vault. Operators populate
secrets via their own tooling (``vault kv put``, Terraform, Vault UI).

Auth modes: ``token`` (dev) and ``approle`` (production). Other modes
(Kubernetes, AWS/GCP IAM, JWT-OIDC, userpass) added per-customer in
future PRs.
"""
from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sagewai.connections.credentials.base import _get_path, _set_path
from sagewai.connections.credentials.errors import (
    VaultAuthError,
    VaultConfigError,
    VaultError,
    VaultReadError,
)
from sagewai.connections.models import HealthResult


class _VaultTokenAuth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["token"] = "token"
    token: str = Field(..., min_length=1)


class _VaultAppRoleAuth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["approle"] = "approle"
    role_id: str = Field(..., min_length=1)
    secret_id: str = Field(..., min_length=1)


class VaultBackendConfig(BaseModel):
    """Per-connection Vault backend configuration."""

    model_config = ConfigDict(extra="forbid")
    url: str = Field(..., pattern=r"^https?://")
    namespace: str | None = None
    mount: str = "secret"
    base_path: str = Field(..., min_length=1)
    auth: _VaultTokenAuth | _VaultAppRoleAuth = Field(..., discriminator="mode")
    verify_tls: bool = True


def _lazy_import_hvac():
    """Lazy-import hvac; raises VaultError with a clear install hint if missing."""
    try:
        import hvac  # type: ignore[import-not-found]
        return hvac
    except ImportError as exc:
        raise VaultError(
            "hvac not installed. Run: pip install sagewai[vault]"
        ) from exc


class VaultBackend:
    """HashiCorp Vault KV v2 credentials backend."""

    id: ClassVar[str] = "vault"
    display_name: ClassVar[str] = "HashiCorp Vault"

    def validate_config(self, backend_config: dict[str, Any]) -> None:
        try:
            VaultBackendConfig.model_validate(backend_config)
        except ValidationError as exc:
            raise VaultConfigError(f"vault backend config invalid: {exc}") from exc

    def encrypt_fields(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_config(backend_config)
        cfg = VaultBackendConfig.model_validate(backend_config)
        out = protocol_data
        for path_str in sensitive_field_paths:
            leaf = _get_path(out, path_str)
            if leaf is None:
                continue
            # Already an in-storage-form marker? leave it.
            if isinstance(leaf, dict) and "$vault" in leaf:
                continue
            # Derive marker key from the LAST segment of the dotted path
            # (e.g., "tokens.access_token" -> "access_token").
            key = path_str.rsplit(".", 1)[-1]
            marker = {"$vault": {"path": cfg.base_path, "key": key}}
            out = _set_path(out, path_str, marker)
        return out

    def decrypt_fields(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_config(backend_config)
        cfg = VaultBackendConfig.model_validate(backend_config)
        # If no marker is present anywhere, skip the Vault round-trip entirely.
        markers_present = False
        for p in sensitive_field_paths:
            leaf = _get_path(protocol_data, p)
            if isinstance(leaf, dict) and "$vault" in leaf:
                markers_present = True
                break
        if not markers_present:
            return protocol_data

        hvac = _lazy_import_hvac()
        client = hvac.Client(
            url=cfg.url,
            namespace=cfg.namespace,
            verify=cfg.verify_tls,
        )

        # Authenticate
        if cfg.auth.mode == "token":
            client.token = cfg.auth.token
        elif cfg.auth.mode == "approle":
            try:
                client.auth.approle.login(
                    role_id=cfg.auth.role_id,
                    secret_id=cfg.auth.secret_id,
                )
            except Exception as exc:
                raise VaultAuthError(
                    f"vault AppRole login failed: {exc}"
                ) from exc

        # Per-(mount, path) cache for this call
        cache: dict[tuple[str, str], dict] = {}

        out = protocol_data
        for path_str in sensitive_field_paths:
            leaf = _get_path(out, path_str)
            if not isinstance(leaf, dict) or "$vault" not in leaf:
                continue
            ref = leaf["$vault"]
            vault_path = ref["path"]
            vault_key = ref["key"]
            cache_key = (cfg.mount, vault_path)
            if cache_key not in cache:
                try:
                    resp = client.secrets.kv.v2.read_secret_version(
                        path=vault_path, mount_point=cfg.mount,
                    )
                except Exception as exc:
                    raise VaultReadError(
                        f"vault read failed for {cfg.mount}/{vault_path}: {exc}"
                    ) from exc
                cache[cache_key] = resp.get("data", {}).get("data", {})
            secret_data = cache[cache_key]
            if vault_key not in secret_data:
                raise VaultReadError(
                    f"vault key {vault_key!r} not found in {cfg.mount}/{vault_path}"
                )
            out = _set_path(out, path_str, secret_data[vault_key])
        return out

    def health(self, backend_config: dict[str, Any]) -> HealthResult:
        self.validate_config(backend_config)
        cfg = VaultBackendConfig.model_validate(backend_config)
        try:
            hvac = _lazy_import_hvac()
        except VaultError as exc:
            return HealthResult(ok=False, message=str(exc))
        try:
            client = hvac.Client(
                url=cfg.url, namespace=cfg.namespace, verify=cfg.verify_tls,
            )
            if cfg.auth.mode == "token":
                client.token = cfg.auth.token
            elif cfg.auth.mode == "approle":
                client.auth.approle.login(
                    role_id=cfg.auth.role_id, secret_id=cfg.auth.secret_id,
                )
            status = client.sys.read_health_status()
            sealed = status.get("sealed", "?")
            init = status.get("initialized", "?")
            return HealthResult(
                ok=True,
                message=f"vault sealed={sealed} initialized={init}",
            )
        except Exception as exc:
            return HealthResult(ok=False, message=f"vault unhealthy: {exc}")


__all__ = [
    "VaultBackend",
    "VaultBackendConfig",
]
