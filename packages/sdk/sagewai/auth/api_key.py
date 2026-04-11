# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""API key authentication — validation and management.

Provides simple API key validation for service-to-service authentication.
Keys can be loaded from environment variables or passed directly.
"""

from __future__ import annotations

import hashlib
import logging
import secrets

logger = logging.getLogger(__name__)

_KEY_PREFIX = "sk-sage-"
_KEY_LENGTH = 32


class APIKeyAuth:
    """API key authenticator.

    Parameters
    ----------
    valid_keys:
        List of valid API keys. Keys are stored as SHA-256 hashes for security.
    """

    def __init__(self, *, valid_keys: list[str] | None = None) -> None:
        self._key_hashes: set[str] = set()
        if valid_keys:
            for key in valid_keys:
                self._key_hashes.add(self._hash_key(key))

    def validate(self, key: str) -> bool:
        """Validate an API key.

        Args:
            key: The API key to validate.

        Returns:
            True if the key is valid.

        Raises:
            AuthenticationError: If the key is invalid.
        """
        from sagewai.auth.jwt import AuthenticationError

        if not key:
            raise AuthenticationError("API key must not be empty")
        if self._hash_key(key) not in self._key_hashes:
            raise AuthenticationError("Invalid API key")
        return True

    def is_valid(self, key: str) -> bool:
        """Check if an API key is valid without raising.

        Args:
            key: The API key to check.

        Returns:
            True if valid, False otherwise.
        """
        if not key:
            return False
        return self._hash_key(key) in self._key_hashes

    def add_key(self, key: str) -> None:
        """Add a valid API key."""
        self._key_hashes.add(self._hash_key(key))

    def revoke_key(self, key: str) -> bool:
        """Revoke an API key.

        Returns:
            True if the key was found and revoked.
        """
        h = self._hash_key(key)
        if h in self._key_hashes:
            self._key_hashes.discard(h)
            return True
        return False

    @property
    def key_count(self) -> int:
        """Number of registered API keys."""
        return len(self._key_hashes)

    @staticmethod
    def generate_key() -> str:
        """Generate a new random API key with the sage prefix.

        Returns:
            A new API key string like ``sk-sage-<random>``.
        """
        random_part = secrets.token_hex(_KEY_LENGTH // 2)
        return f"{_KEY_PREFIX}{random_part}"

    @staticmethod
    def _hash_key(key: str) -> str:
        """Hash an API key for secure storage."""
        return hashlib.sha256(key.encode()).hexdigest()
