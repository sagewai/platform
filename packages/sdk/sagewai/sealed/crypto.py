# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Fernet wrapping — single module for all Sealed-i crypto.

Algorithm choice (Fernet) is private to this module so it can be swapped
later (AES-GCM-256, libsodium SecretBox) without ripple effects.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretCorrupted(RuntimeError):  # noqa: N818
    """Raised when a Fernet decrypt fails — likely tampered or wrong key."""


class Crypto:
    """Encrypt/decrypt single secret values with the configured master key."""

    PREFIX = "fernet:"

    def __init__(
        self,
        master_key: bytes,
        *,
        previous_keys: list[bytes] | None = None,
    ) -> None:
        self._fernets = [Fernet(master_key)]
        if previous_keys:
            self._fernets.extend(Fernet(k) for k in previous_keys)
        self._multi = MultiFernet(self._fernets)

    def encrypt(self, plaintext: str) -> str:
        token = self._multi.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{self.PREFIX}{token}"

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(self.PREFIX):
            raise SecretCorrupted("missing fernet prefix; profile may be corrupt")
        try:
            raw = self._multi.decrypt(ciphertext[len(self.PREFIX):].encode("ascii"))
        except InvalidToken as exc:
            raise SecretCorrupted("Fernet decrypt failed; key wrong or value tampered") from exc
        return raw.decode("utf-8")

    def rotate_value(self, ciphertext: str) -> str:
        """Re-encrypt with the primary key — used during master-key rotation."""
        plaintext = self.decrypt(ciphertext)
        return self.encrypt(plaintext)
