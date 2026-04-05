# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Token manager — high-level API for access token lifecycle."""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid

from sagewai.gateway.models import AccessToken, TokenStatus
from sagewai.gateway.store import TokenStore

logger = logging.getLogger(__name__)

_TOKEN_PREFIX = "sat-"
_TOKEN_BYTES = 32


class TokenManager:
    """High-level API for creating, validating, and revoking access tokens.

    Parameters
    ----------
    store:
        TokenStore backend for persistence.
    default_expiry_seconds:
        Default token lifetime (default: 24 hours).
    """

    def __init__(
        self,
        store: TokenStore,
        *,
        default_expiry_seconds: int = 86400,
    ) -> None:
        self.store = store
        self.default_expiry_seconds = default_expiry_seconds

    async def generate(
        self,
        *,
        agent_name: str,
        grantor_id: str,
        scopes: list[str] | None = None,
        expires_in_seconds: int | None = None,
        single_use: bool = False,
    ) -> str:
        """Generate a new access token.

        Returns the plaintext token string (``sat-<hex>``).
        Only the SHA-256 hash is stored.
        """
        raw = secrets.token_hex(_TOKEN_BYTES)
        plaintext = f"{_TOKEN_PREFIX}{raw}"
        token_hash = hashlib.sha256(plaintext.encode()).hexdigest()

        expiry = (
            expires_in_seconds if expires_in_seconds is not None else self.default_expiry_seconds
        )

        token = AccessToken(
            token_id=str(uuid.uuid4()),
            token_hash=token_hash,
            token_suffix=plaintext[-4:],
            agent_name=agent_name,
            grantor_id=grantor_id,
            scopes=scopes or ["chat"],
            single_use=single_use,
            expires_at=time.time() + expiry,
        )
        await self.store.save(token)
        logger.info("Generated access token %s for agent %s", token.token_id, agent_name)
        return plaintext

    async def validate(self, plaintext: str) -> AccessToken | None:
        """Validate a plaintext token.

        Returns the AccessToken if valid, None otherwise.
        For single-use tokens, marks as used on first successful validation.
        """
        token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        token = await self.store.get_by_hash(token_hash)

        if token is None:
            return None
        if not token.is_usable:
            return None

        if token.single_use:
            token.used_at = time.time()
            token.status = TokenStatus.USED
            await self.store.save(token)

        return token

    async def revoke(self, token_id: str) -> None:
        """Revoke an access token by ID."""
        await self.store.revoke(token_id)
        logger.info("Revoked access token %s", token_id)

    async def list_tokens(
        self, agent_name: str | None = None, limit: int = 50
    ) -> list[AccessToken]:
        """List tokens, optionally filtered by agent."""
        return await self.store.list_tokens(agent_name=agent_name, limit=limit)
