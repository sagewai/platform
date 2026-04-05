# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Token store — protocol and in-memory implementation."""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from sagewai.gateway.models import AccessToken, TokenStatus


@runtime_checkable
class TokenStore(Protocol):
    """Protocol for access token persistence backends."""

    async def save(self, token: AccessToken) -> None: ...

    async def get(self, token_id: str) -> AccessToken | None: ...

    async def get_by_hash(self, token_hash: str) -> AccessToken | None: ...

    async def list_tokens(
        self, agent_name: str | None = None, limit: int = 50
    ) -> list[AccessToken]: ...

    async def revoke(self, token_id: str) -> None: ...

    async def delete(self, token_id: str) -> None: ...

    async def cleanup_expired(self) -> int: ...


class InMemoryTokenStore:
    """In-memory TokenStore for testing."""

    def __init__(self) -> None:
        self._tokens: dict[str, AccessToken] = {}

    async def save(self, token: AccessToken) -> None:
        self._tokens[token.token_id] = token

    async def get(self, token_id: str) -> AccessToken | None:
        return self._tokens.get(token_id)

    async def get_by_hash(self, token_hash: str) -> AccessToken | None:
        for token in self._tokens.values():
            if token.token_hash == token_hash:
                return token
        return None

    async def list_tokens(
        self, agent_name: str | None = None, limit: int = 50
    ) -> list[AccessToken]:
        tokens = list(self._tokens.values())
        if agent_name:
            tokens = [t for t in tokens if t.agent_name == agent_name]
        tokens.sort(key=lambda t: t.created_at, reverse=True)
        return tokens[:limit]

    async def revoke(self, token_id: str) -> None:
        token = self._tokens.get(token_id)
        if token:
            token.status = TokenStatus.REVOKED

    async def delete(self, token_id: str) -> None:
        self._tokens.pop(token_id, None)

    async def cleanup_expired(self) -> int:
        now = time.time()
        expired_ids = [tid for tid, t in self._tokens.items() if t.expires_at < now]
        for tid in expired_ids:
            del self._tokens[tid]
        return len(expired_ids)
