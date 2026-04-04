# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""PostgreSQL-backed token store using asyncpg."""

from __future__ import annotations

import logging
from typing import Any

from sagewai.gateway.models import AccessToken, TokenStatus

logger = logging.getLogger(__name__)


class PostgresTokenStore:
    """TokenStore backed by PostgreSQL via asyncpg.

    Parameters
    ----------
    database_url:
        PostgreSQL connection string.
    pool:
        Optional pre-existing asyncpg pool (for sharing with other stores).
    """

    def __init__(
        self,
        database_url: str = "",
        *,
        pool: Any = None,
    ) -> None:
        self._database_url = database_url
        self._pool = pool

    async def init(self) -> None:
        """Initialize the connection pool if not provided."""
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=5)

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()

    async def save(self, token: AccessToken) -> None:
        """Insert or update an access token."""
        await self._pool.execute(
            """
            INSERT INTO agent_access_tokens
                (token_id, token_hash, token_suffix, agent_name, grantor_id, scopes,
                 status, single_use, expires_at, used_at, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                    to_timestamp($9), to_timestamp($10), to_timestamp($11))
            ON CONFLICT (token_id) DO UPDATE SET
                status = $7,
                used_at = to_timestamp($10)
            """,
            token.token_id,
            token.token_hash,
            token.token_suffix,
            token.agent_name,
            token.grantor_id,
            token.scopes,
            token.status.value,
            token.single_use,
            token.expires_at,
            token.used_at,
            token.created_at,
        )

    async def get(self, token_id: str) -> AccessToken | None:
        """Get a token by ID."""
        row = await self._pool.fetchrow(
            "SELECT * FROM agent_access_tokens WHERE token_id = $1", token_id
        )
        return self._row_to_token(row) if row else None

    async def get_by_hash(self, token_hash: str) -> AccessToken | None:
        """Get a token by its SHA-256 hash."""
        row = await self._pool.fetchrow(
            "SELECT * FROM agent_access_tokens WHERE token_hash = $1", token_hash
        )
        return self._row_to_token(row) if row else None

    async def list_tokens(
        self, agent_name: str | None = None, limit: int = 50
    ) -> list[AccessToken]:
        """List tokens, optionally filtered by agent."""
        if agent_name:
            rows = await self._pool.fetch(
                "SELECT * FROM agent_access_tokens WHERE agent_name = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                agent_name,
                limit,
            )
        else:
            rows = await self._pool.fetch(
                "SELECT * FROM agent_access_tokens ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        return [self._row_to_token(row) for row in rows]

    async def revoke(self, token_id: str) -> None:
        """Revoke a token by ID."""
        await self._pool.execute(
            "UPDATE agent_access_tokens SET status = $1 WHERE token_id = $2",
            TokenStatus.REVOKED.value,
            token_id,
        )

    async def delete(self, token_id: str) -> None:
        """Delete a token by ID."""
        await self._pool.execute("DELETE FROM agent_access_tokens WHERE token_id = $1", token_id)

    async def cleanup_expired(self) -> int:
        """Remove expired tokens. Returns count removed."""
        result = await self._pool.execute(
            "DELETE FROM agent_access_tokens WHERE expires_at < now()"
        )
        count = int(result.split()[-1]) if result else 0
        logger.info("Cleaned up %d expired access tokens", count)
        return count

    @staticmethod
    def _row_to_token(row: Any) -> AccessToken:
        """Convert a database row to an AccessToken."""
        return AccessToken(
            token_id=row["token_id"],
            token_hash=row["token_hash"],
            token_suffix=row["token_suffix"],
            agent_name=row["agent_name"],
            grantor_id=row["grantor_id"],
            scopes=row["scopes"],
            status=TokenStatus(row["status"]),
            single_use=row["single_use"],
            expires_at=row["expires_at"].timestamp(),
            used_at=row["used_at"].timestamp() if row["used_at"] else None,
            created_at=row["created_at"].timestamp(),
        )
