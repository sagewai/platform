# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Local (Fernet) credentials backend.

Wraps :class:`sagewai.sealed.crypto.Crypto`. Ciphertexts are stored
in-place inside the connection record (per the connections.json layout
from PR1). ``backend_config`` is empty — the master key resolves via
:func:`sagewai.sealed.master_key.resolve_master_key` (env, vault file,
or AdminStateFile, per existing platform conventions).
"""
from __future__ import annotations

from typing import Any, ClassVar

from sagewai.connections.credentials.base import _get_path, _set_path
from sagewai.connections.credentials.errors import (
    BackendUnhealthyError,
    InvalidBackendConfigError,
)
from sagewai.connections.models import HealthResult
from sagewai.sealed.crypto import Crypto, SecretCorrupted
from sagewai.sealed.master_key import MasterKeyMissing, resolve_master_key


def _crypto() -> Crypto:
    """Resolve the platform master key + wrap a Crypto instance."""
    key, _ = resolve_master_key()
    return Crypto(key)


class LocalBackend:
    """Fernet-encrypted in-place storage. Default for fresh installs."""

    id: ClassVar[str] = "local"
    display_name: ClassVar[str] = "Local encrypted file"

    def encrypt_fields(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_config(backend_config)
        crypto = _crypto()
        out = protocol_data
        for path in sensitive_field_paths:
            leaf = _get_path(out, path)
            if leaf is None or not isinstance(leaf, str):
                continue  # nothing there or already non-string (e.g., a marker)
            if leaf.startswith(Crypto.PREFIX):
                continue  # already encrypted; idempotent re-encrypt
            out = _set_path(out, path, crypto.encrypt(leaf))
        return out

    def decrypt_fields(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_config(backend_config)
        crypto = _crypto()
        out = protocol_data
        for path in sensitive_field_paths:
            leaf = _get_path(out, path)
            if leaf is None or not isinstance(leaf, str):
                continue
            if not leaf.startswith(Crypto.PREFIX):
                continue  # already plaintext (e.g., never encrypted)
            try:
                plaintext = crypto.decrypt(leaf)
            except SecretCorrupted as exc:
                raise BackendUnhealthyError(
                    f"local backend cannot decrypt {path!r}: {exc}"
                ) from exc
            out = _set_path(out, path, plaintext)
        return out

    def health(self, backend_config: dict[str, Any]) -> HealthResult:
        self.validate_config(backend_config)
        try:
            resolve_master_key()
        except MasterKeyMissing as exc:
            return HealthResult(ok=False, message=f"master key not set: {exc}")
        except Exception as exc:
            return HealthResult(ok=False, message=f"master key error: {exc}")
        return HealthResult(ok=True, message="local backend ready")

    def validate_config(self, backend_config: dict[str, Any]) -> None:
        if backend_config:
            raise InvalidBackendConfigError(
                f"local backend takes no config; got {sorted(backend_config)!r}"
            )


__all__ = ["LocalBackend"]
