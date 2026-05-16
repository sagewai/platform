# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""VaultBackend — Sealed Identity backend backed by HashiCorp Vault KV v2.

See docs/superpowers/specs/2026-04-28-sealed-ii-vault-design.md.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

try:
    import hvac
    from hvac.exceptions import InvalidPath
except ImportError as e:  # pragma: no cover - import-time guard
    raise ImportError(
        "VaultBackend requires hvac. Install with `pip install sagewai[vault]`."
    ) from e

from sagewai.sealed.audit import AuditWriter
from sagewai.sealed.backend import (
    BackendUnsupportedOperationError,
    ProfileNotFoundError,
    VaultAuthError,
    VaultConfigError,
    VaultUnreachableError,
)
from sagewai.sealed.models import Profile, ProfileMetadata, ProfileWritePayload

_SUPPORTED_AUTH_METHODS = frozenset({"token", "approle", "kubernetes"})


class VaultBackend:
    """Sealed-ii.Vault — HashiCorp Vault KV v2 as the value source for profiles.

    Profiles are stored as one KV v2 item per profile_id, with the JSON
    body matching ProfileWritePayload (no per-secret Fernet wrapping;
    Vault encrypts at rest natively).

    Three auth methods are supported in v1: token, AppRole, Kubernetes SA.
    Vault Enterprise namespaces are supported via the `namespace` parameter.
    """

    name = "vault"
    scheme = "vault"

    def __init__(
        self,
        *,
        addr: str,
        namespace: str | None,
        auth_method: str,
        auth_config: dict[str, Any],
        mount: str,
        audit_writer: AuditWriter | None = None,
        path_prefix: str = "",
        capture_request_id: bool = True,
    ) -> None:
        if auth_method not in _SUPPORTED_AUTH_METHODS:
            raise VaultConfigError(
                f"unsupported auth_method {auth_method!r}; "
                f"supported: {sorted(_SUPPORTED_AUTH_METHODS)}"
            )
        self._addr = addr
        self._namespace = namespace
        self._auth_method = auth_method
        self._auth_config = auth_config
        self._mount = mount
        self._audit = audit_writer
        self._path_prefix = path_prefix
        self._capture_request_id = capture_request_id
        self._startup_audit_emitted = False

        self._validate_auth_config()
        self._client = self._build_client()

    def _validate_auth_config(self) -> None:
        cfg = self._auth_config
        if self._auth_method == "approle":
            if not cfg.get("role_id"):
                raise VaultConfigError("approle auth requires auth_config.role_id")
            if not cfg.get("secret_id_env"):
                raise VaultConfigError(
                    "approle auth requires auth_config.secret_id_env (env var name)"
                )
        elif self._auth_method == "kubernetes":
            if not cfg.get("role"):
                raise VaultConfigError("kubernetes auth requires auth_config.role")
        # token validation happens in _resolve_token() called by _build_client()

    def _build_client(self) -> hvac.Client:
        kwargs: dict[str, Any] = {"url": self._addr}
        if self._namespace:
            kwargs["namespace"] = self._namespace
        if self._auth_method == "token":
            token = self._resolve_token()
            kwargs["token"] = token
        return hvac.Client(**kwargs)

    def _resolve_token(self) -> str:
        cfg = self._auth_config
        if "token" in cfg and cfg["token"]:
            return cfg["token"]
        if "token_env" in cfg and cfg["token_env"]:
            env_var = cfg["token_env"]
            if env_var not in os.environ:
                raise VaultConfigError(
                    f"auth_config.token_env={env_var!r} but env var is not set"
                )
            return os.environ[env_var]
        raise VaultConfigError(
            "token auth requires auth_config.token or auth_config.token_env"
        )

    async def _ensure_authenticated(self) -> None:
        if self._client.is_authenticated():
            return
        await self._login()

    async def _login(self) -> None:
        cfg = self._auth_config
        if self._auth_method == "token":
            # No re-login for token auth; if invalid we surface on next call.
            return
        if self._auth_method == "approle":
            secret_id = os.environ[cfg["secret_id_env"]]
            self._client.auth.approle.login(
                role_id=cfg["role_id"], secret_id=secret_id,
            )
            return
        if self._auth_method == "kubernetes":
            token_path = cfg.get(
                "token_path",
                "/var/run/secrets/kubernetes.io/serviceaccount/token",
            )
            with open(token_path, encoding="utf-8") as f:
                jwt = f.read()
            self._client.auth.kubernetes.login(
                role=cfg["role"], jwt=jwt,
            )
            return
        # _SUPPORTED_AUTH_METHODS gate in __init__ should prevent this
        raise VaultConfigError(f"no login impl for {self._auth_method!r}")

    def _safe(self, fn, *args, **kwargs):
        """Translate hvac transport errors to our BackendTransportError family.

        InvalidPath is re-raised unchanged so per-method handlers can map
        it to ProfileNotFoundError where appropriate.
        """
        try:
            return fn(*args, **kwargs)
        except InvalidPath:
            raise
        except Exception as e:
            from hvac import exceptions as hvexc
            if isinstance(e, (hvexc.Forbidden, hvexc.Unauthorized)):
                raise VaultAuthError(str(e)) from e
            if isinstance(e, hvexc.VaultDown):
                raise VaultUnreachableError(str(e)) from e
            if isinstance(e, (ConnectionError, TimeoutError)):
                raise VaultUnreachableError(str(e)) from e
            raise

    def _resolve_path(self, profile_id: str) -> str:
        if self._path_prefix:
            return f"{self._path_prefix.rstrip('/')}/{profile_id}"
        return profile_id

    async def _emit(
        self,
        *,
        event_type: str,
        profile_id: str | None = None,
        secret_key: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.emit(
            event_type=event_type,
            profile_id=profile_id,
            secret_key=secret_key,
            details=details or {},
        )

    async def get_profile(self, profile_id: str) -> Profile:
        await self._ensure_authenticated()
        path = self._resolve_path(profile_id)
        try:
            resp = self._safe(
                self._client.secrets.kv.v2.read_secret_version,
                path=path, mount_point=self._mount,
            )
        except InvalidPath as e:
            raise ProfileNotFoundError(profile_id) from e

        body = resp["data"]["data"]
        meta_block = resp["data"].get("metadata", {})
        request_id = resp.get("request_id") if self._capture_request_id else None
        secrets = dict(body.get("secrets", {}))
        env = dict(body.get("env", {}))

        for key in secrets:
            details: dict[str, Any] = {"purpose": "get_profile"}
            if request_id:
                details["vault_request_id"] = request_id
            await self._emit(
                event_type="secret.decrypted",
                profile_id=profile_id,
                secret_key=key,
                details=details,
            )

        last_rotated_at = None
        if meta_block.get("created_time"):
            last_rotated_at = datetime.fromisoformat(
                meta_block["created_time"].replace("Z", "+00:00")
            )
        return Profile(
            id=profile_id,
            name=body.get("name", profile_id),
            description=body.get("description", ""),
            owner=body.get("owner"),
            tags=list(body.get("tags", [])),
            last_rotated_at=last_rotated_at,
            allowed_workflows=list(body.get("allowed_workflows", [])),
            env=env,
            secret_keys=sorted(secrets.keys()),
            secrets=secrets,
        )

    async def save_profile(self, payload: ProfileWritePayload) -> Profile:
        if not payload.id:
            raise ValueError("ProfileWritePayload.id is required for save_profile")
        await self._ensure_authenticated()
        path = self._resolve_path(payload.id)

        existed = True
        try:
            self._safe(
                self._client.secrets.kv.v2.read_secret_metadata,
                path=path, mount_point=self._mount,
            )
        except InvalidPath:
            existed = False

        body = {
            "name": payload.name,
            "description": payload.description,
            "owner": payload.owner,
            "tags": list(payload.tags),
            "allowed_workflows": list(payload.allowed_workflows),
            "env": dict(payload.env),
            "secrets": dict(payload.secrets),
        }
        write_resp = self._safe(
            self._client.secrets.kv.v2.create_or_update_secret,
            path=path, secret=body, mount_point=self._mount,
        )

        custom = {
            "name": payload.name,
            "description": payload.description,
            "owner": payload.owner or "",
            "tags": ",".join(payload.tags),
            "env_keys": ",".join(sorted(payload.env.keys())),
            "secret_keys": ",".join(sorted(payload.secrets.keys())),
            "allowed_workflows": ",".join(payload.allowed_workflows),
        }
        self._safe(
            self._client.secrets.kv.v2.update_metadata,
            path=path, custom_metadata=custom, mount_point=self._mount,
        )

        details: dict[str, Any] = {
            "name": payload.name,
            "secret_keys": sorted(payload.secrets.keys()),
            "env_keys": sorted(payload.env.keys()),
        }
        if self._capture_request_id and isinstance(write_resp, dict):
            rid = write_resp.get("request_id")
            if rid:
                details["vault_request_id"] = rid
            v = write_resp.get("data", {}).get("version")
            if v is not None:
                details["vault_version_id"] = v

        await self._emit(
            event_type="profile.updated" if existed else "profile.created",
            profile_id=payload.id,
            details=details,
        )
        return await self.get_profile(payload.id)

    async def delete_profile(self, profile_id: str) -> None:
        await self._ensure_authenticated()
        path = self._resolve_path(profile_id)
        try:
            self._safe(
                self._client.secrets.kv.v2.read_secret_metadata,
                path=path, mount_point=self._mount,
            )
        except InvalidPath as e:
            raise ProfileNotFoundError(profile_id) from e
        self._safe(
            self._client.secrets.kv.v2.delete_metadata_and_all_versions,
            path=path, mount_point=self._mount,
        )
        await self._emit(event_type="profile.deleted", profile_id=profile_id)

    async def list_profiles(self) -> list[ProfileMetadata]:
        await self._ensure_authenticated()
        try:
            resp = self._safe(
                self._client.secrets.kv.v2.list_secrets,
                path=self._path_prefix or "",
                mount_point=self._mount,
            )
        except InvalidPath:
            return []
        keys = resp["data"]["keys"]
        results: list[ProfileMetadata] = []
        for k in keys:
            if k.endswith("/"):
                continue  # nested directory — skip
            try:
                results.append(await self.get_profile_metadata(k))
            except ProfileNotFoundError:
                continue  # raced with a delete
        return results

    async def get_profile_metadata(self, profile_id: str) -> ProfileMetadata:
        await self._ensure_authenticated()
        path = self._resolve_path(profile_id)
        try:
            resp = self._safe(
                self._client.secrets.kv.v2.read_secret_metadata,
                path=path, mount_point=self._mount,
            )
        except InvalidPath as e:
            raise ProfileNotFoundError(profile_id) from e
        data = resp["data"]
        custom = data.get("custom_metadata") or {}
        secret_keys = [
            k.strip()
            for k in str(custom.get("secret_keys", "")).split(",")
            if k.strip()
        ]
        env_keys = [
            k.strip()
            for k in str(custom.get("env_keys", "")).split(",")
            if k.strip()
        ]
        tags = [
            t.strip()
            for t in str(custom.get("tags", "")).split(",")
            if t.strip()
        ]
        last_rotated_at = None
        if data.get("created_time"):
            last_rotated_at = datetime.fromisoformat(
                data["created_time"].replace("Z", "+00:00")
            )
        return ProfileMetadata(
            id=profile_id,
            name=custom.get("name") or profile_id,
            description=custom.get("description", ""),
            owner=custom.get("owner") or None,
            tags=tags,
            last_rotated_at=last_rotated_at,
            allowed_workflows=[
                w.strip()
                for w in str(custom.get("allowed_workflows", "")).split(",")
                if w.strip()
            ],
            env={k: "" for k in env_keys},
            secret_keys=secret_keys,
        )

    async def supports_master_key_rotation(self) -> bool:
        return False

    async def rotate_master_key(self, new_key: bytes) -> int:
        raise BackendUnsupportedOperationError(
            "Vault master key is rotated via Vault primitives — see "
            "https://developer.hashicorp.com/vault/docs/concepts/seal"
        )


def build_vault_backend_from_config(
    cfg: dict[str, Any],
    audit_writer: AuditWriter | None = None,
) -> VaultBackend | None:
    """Build a VaultBackend from admin-state.sealed.vault config.

    Returns None when cfg.enabled is False (or missing). Otherwise
    constructs a VaultBackend with config validation.
    Raises VaultConfigError on malformed config.
    """
    if not cfg or not cfg.get("enabled"):
        return None
    addr = cfg.get("addr")
    if not addr:
        raise VaultConfigError(
            "sealed.vault.addr is required when sealed.vault.enabled = true"
        )
    return VaultBackend(
        addr=addr,
        namespace=cfg.get("namespace"),
        auth_method=cfg.get("auth_method", "token"),
        auth_config=cfg.get("auth_config", {}) or {},
        mount=cfg.get("mount", "kv"),
        path_prefix=cfg.get("path_prefix", ""),
        capture_request_id=cfg.get("audit_request_id_capture", True),
        audit_writer=audit_writer,
    )
