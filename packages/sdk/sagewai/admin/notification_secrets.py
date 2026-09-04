# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Notification secret fields and the one fail-closed decrypt both readers share.

The admin's send and test paths and the coordinator's channel resolver read the same rows.
Neither may fall back to ciphertext or to a plaintext passthrough, so the decrypt lives here
rather than in a closure over a ``Request``.
"""

from __future__ import annotations

from typing import Any

from sagewai.admin import tenant_keys
from sagewai.admin.identity_store import IdentityStore
from sagewai.sealed.crypto import SecretCorrupted

NOTIFICATION_SECRET_KEYS = frozenset(
    {
        "webhook_url",
        "email_api_key",
        "smtp_password",
        "api_key",
        "token",
        "secret",
        "password",
    }
)


class NotificationSecretDecryptionError(RuntimeError):
    """A stored notification secret could not be decrypted; the caller fails closed."""


async def decrypt_notification_secrets(
    record: dict[str, Any], *, identity_store: IdentityStore, org_id: str
) -> dict[str, Any]:
    """A copy of ``record`` with every secret decrypted under the row's own project key.

    A row with no ``project_id`` is org-shared and decrypts under the org master key.
    """
    row_project_id = record.get("project_id") or None
    out = dict(record)
    for key in NOTIFICATION_SECRET_KEYS:
        value = out.get(key)
        if isinstance(value, str) and value:
            try:
                out[key] = await tenant_keys.decrypt_for_project(
                    identity_store, org_id, row_project_id, value
                )
            except SecretCorrupted as exc:
                raise NotificationSecretDecryptionError(
                    "notification secret could not be decrypted"
                ) from exc
    return out


__all__ = [
    "NOTIFICATION_SECRET_KEYS",
    "NotificationSecretDecryptionError",
    "decrypt_notification_secrets",
]
