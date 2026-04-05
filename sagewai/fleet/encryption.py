# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Fleet payload encryption using per-org Fernet keys.

Encrypts and decrypts workflow run inputs/outputs at rest so that
sensitive payloads are never stored in plaintext.  Each organization
gets its own symmetric Fernet key; if no key is configured the
module acts as a transparent passthrough (backward-compatible).

Usage::

    from sagewai.fleet.encryption import FleetPayloadEncryption

    enc = FleetPayloadEncryption(org_keys={"acme": FleetPayloadEncryption.generate_key()})

    ciphertext = enc.encrypt("acme", '{"task": "run report"}')
    plaintext  = enc.decrypt("acme", ciphertext)

The ``cryptography`` package is imported lazily so the rest of the SDK
works fine without it installed; an ``ImportError`` is raised only when
encryption is actually attempted.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class FleetPayloadEncryption:
    """Encrypt/decrypt workflow run inputs and outputs using per-org Fernet keys."""

    def __init__(self, org_keys: dict[str, str] | None = None) -> None:
        """Initialize with optional per-org Fernet keys.

        Args:
            org_keys: Mapping of ``org_id`` to base64-encoded Fernet key.
                If *None* or empty, encryption is disabled (passthrough).
        """
        self._fernets: dict[str, object] = {}
        if org_keys:
            from cryptography.fernet import Fernet

            for org_id, key in org_keys.items():
                raw = key.encode() if isinstance(key, str) else key
                self._fernets[org_id] = Fernet(raw)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encrypt(self, org_id: str, payload: str) -> str:
        """Encrypt a JSON string payload.

        Returns base64 ciphertext.  If no key exists for *org_id* the
        payload is returned unchanged.
        """
        fernet = self._fernets.get(org_id)
        if fernet is None:
            return payload
        # Fernet.encrypt returns bytes; decode to str for JSON storage.
        return fernet.encrypt(payload.encode()).decode()  # type: ignore[union-attr]

    def decrypt(self, org_id: str, ciphertext: str) -> str:
        """Decrypt a base64 ciphertext.

        Returns the original JSON string.  If no key exists for *org_id*
        the ciphertext is returned unchanged.
        """
        fernet = self._fernets.get(org_id)
        if fernet is None:
            return ciphertext
        return fernet.decrypt(ciphertext.encode()).decode()  # type: ignore[union-attr]

    def has_key(self, org_id: str) -> bool:
        """Check whether encryption is available for *org_id*."""
        return org_id in self._fernets

    @staticmethod
    def generate_key() -> str:
        """Generate a new Fernet key (base64-encoded string)."""
        from cryptography.fernet import Fernet

        return Fernet.generate_key().decode()
