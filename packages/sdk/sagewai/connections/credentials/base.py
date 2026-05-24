# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Credentials backend Protocol + sensitive-field path helpers.

Each backend implements :class:`CredentialsBackend`. The protocol's
``encrypt_fields`` and ``decrypt_fields`` walk a connection's
``protocol_data`` along JSON-pointer-ish dotted paths (e.g.,
``tokens.access_token``) supplied by the protocol plugin's
``sensitive_fields`` ClassVar.

The two private helpers ``_get_path`` and ``_set_path`` are the
non-third-party walking primitives. Both are conservative: only existing
leaves are returned / updated. New keys are NOT created on set —
encryption applies to fields the plugin already wrote.
"""
from __future__ import annotations

import copy
from typing import Any, ClassVar, Protocol, runtime_checkable

from sagewai.connections.models import HealthResult


def _get_path(data: dict[str, Any], path: str) -> Any | None:
    """Return the value at ``path`` (dotted) in ``data``, or None."""
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_path(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Return a deep-copy of ``data`` with ``path`` updated to ``value``.

    If any intermediate path segment is missing OR not a dict, the
    return value is the unchanged deep-copy. This is intentional:
    encryption is only applied to fields the plugin already wrote.
    """
    out = copy.deepcopy(data)
    parts = path.split(".")
    cur: Any = out
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur or not isinstance(cur[part], dict):
            return out  # path traversal blocked; no-op
        cur = cur[part]
    leaf = parts[-1]
    if isinstance(cur, dict) and leaf in cur:
        cur[leaf] = value
    return out


@runtime_checkable
class CredentialsBackend(Protocol):
    """Contract every credentials backend implements.

    Backends are stateless singletons held in
    :data:`sagewai.connections.credentials.BACKENDS`. The
    :class:`CredentialsBackendRouter` (Task 6) routes encrypt/decrypt
    calls to the right backend based on each connection's
    ``credentials_backend`` field.
    """

    id: ClassVar[str]                # "local" | "env" | "sops"
    display_name: ClassVar[str]      # "Local encrypted file" | "Environment variables" | "Mozilla SOPS"

    def encrypt_fields(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a new ``protocol_data`` with each sensitive path's
        leaf replaced by the backend's stored form.

        ``local``: ciphertext string (Fernet output) in-place.
        ``env``:  marker ``{"$env": "<VAR_NAME>"}`` in-place; original
                  value is NOT stored anywhere.
        ``sops``: marker ``{"$sops": {"file": "...", "key": "..."}}``
                  in-place; original value is NOT stored; operator
                  manages the SOPS file separately.
        """
        ...

    def decrypt_fields(
        self,
        protocol_data: dict[str, Any],
        *,
        sensitive_field_paths: tuple[str, ...],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Inverse of :meth:`encrypt_fields` — returns plaintext leaves."""
        ...

    def health(self, backend_config: dict[str, Any]) -> HealthResult:
        """Self-check the backend is operable for this config.

        ``local`` — verifies master key resolves.
        ``env``   — verifies every declared env var is set.
        ``sops``  — verifies SOPS binary on PATH + age key readable.
        """
        ...

    def validate_config(self, backend_config: dict[str, Any]) -> None:
        """Pydantic-style validation; raises ``InvalidBackendConfigError`` on bad config."""
        ...


__all__ = ["CredentialsBackend", "_get_path", "_set_path"]
