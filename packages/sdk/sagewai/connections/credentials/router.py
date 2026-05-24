# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Per-connection credentials backend router.

Plugins (PR2's ``PluginContext.creds``, wired by PR4) receive an
instance of :class:`CredentialsBackendRouter`. The router selects the
right backend per connection (per-connection ``credentials_backend``
overrides; falls back to the platform default), dispatches
encrypt/decrypt/health, and exposes :meth:`swap` for the
backend-change flow (PATCH /api/v1/admin/connections/{id} in PR4).
"""
from __future__ import annotations

from typing import Any

from sagewai.connections.credentials.base import CredentialsBackend
from sagewai.connections.credentials.env import EnvBackend
from sagewai.connections.credentials.errors import UnknownBackendError
from sagewai.connections.credentials.local import LocalBackend
from sagewai.connections.credentials.sops import SopsBackend
from sagewai.connections.models import HealthResult


BACKENDS: tuple[CredentialsBackend, ...] = (
    LocalBackend(),
    EnvBackend(),
    SopsBackend(),
)
_BY_ID: dict[str, CredentialsBackend] = {b.id: b for b in BACKENDS}


def get_backend(backend_id: str) -> CredentialsBackend:
    """Look up a backend by id; raises :class:`UnknownBackendError`."""
    try:
        return _BY_ID[backend_id]
    except KeyError as exc:
        raise UnknownBackendError(backend_id) from exc


def all_backends() -> tuple[CredentialsBackend, ...]:
    """Return every registered backend in declaration order."""
    return BACKENDS


class CredentialsBackendRouter:
    """Routes encrypt/decrypt calls to the right backend per connection."""

    def __init__(self, *, default_backend: str = "local") -> None:
        # Validate default at construction time so misconfigurations
        # surface early, not on the first request.
        get_backend(default_backend)
        self._default_backend = default_backend

    def get_backend_for(
        self,
        connection_credentials_backend: dict[str, Any] | None,
    ) -> tuple[CredentialsBackend, dict[str, Any]]:
        """Return ``(backend, backend_config)`` for a connection.

        Per-connection ``credentials_backend`` wins; falls back to the
        platform default. ``backend_config`` is the operator-supplied
        config dict (empty for ``local``).
        """
        if connection_credentials_backend:
            backend_id = connection_credentials_backend.get("kind")
            backend_config = connection_credentials_backend.get("config", {}) or {}
        else:
            backend_id = self._default_backend
            backend_config = {}
        if backend_id is None:
            raise UnknownBackendError(
                "connection.credentials_backend missing 'kind' field"
            )
        return get_backend(backend_id), backend_config

    def encrypt(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        connection_credentials_backend: dict[str, Any] | None,
    ) -> dict[str, Any]:
        backend, config = self.get_backend_for(connection_credentials_backend)
        return backend.encrypt_fields(
            protocol_data,
            sensitive_field_paths=sensitive_field_paths,
            backend_config=config,
        )

    def decrypt(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        connection_credentials_backend: dict[str, Any] | None,
    ) -> dict[str, Any]:
        backend, config = self.get_backend_for(connection_credentials_backend)
        return backend.decrypt_fields(
            protocol_data,
            sensitive_field_paths=sensitive_field_paths,
            backend_config=config,
        )

    def swap(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        old_credentials_backend: dict[str, Any] | None,
        new_credentials_backend: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Re-encrypt under a new backend.

        Decrypts with the old backend (raising the old backend's
        :class:`CredentialsError` subclasses on failure), then encrypts
        with the new backend.
        """
        decrypted = self.decrypt(
            protocol_data,
            sensitive_field_paths=sensitive_field_paths,
            connection_credentials_backend=old_credentials_backend,
        )
        return self.encrypt(
            decrypted,
            sensitive_field_paths=sensitive_field_paths,
            connection_credentials_backend=new_credentials_backend,
        )

    def health(
        self,
        connection_credentials_backend: dict[str, Any] | None,
    ) -> HealthResult:
        backend, config = self.get_backend_for(connection_credentials_backend)
        return backend.health(config)


__all__ = [
    "BACKENDS",
    "CredentialsBackendRouter",
    "all_backends",
    "get_backend",
]
