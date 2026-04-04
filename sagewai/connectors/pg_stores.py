# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Postgres-backed implementations of connector stores."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sagewai.connectors.base import ConnectorStatus, TokenSet
from sagewai.connectors.stores import CredentialStore, CursorStore, OAuthTokenStore
from sagewai.core.context import resolve_project_id


class PostgresCredentialStore(CredentialStore):
    """Credential store backed by the ``connector_credentials`` table."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get(
        self, connector_name: str, *, project_id: str | None = None
    ) -> dict[str, str] | None:
        pid = resolve_project_id(project_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT config FROM connector_credentials "
                "WHERE project_id = $1 AND connector_name = $2",
                pid,
                connector_name,
            )
            if row and row["config"]:
                val = row["config"]
                return json.loads(val) if isinstance(val, str) else val
            return None

    async def put(
        self,
        connector_name: str,
        credentials: dict[str, str],
        *,
        project_id: str | None = None,
    ) -> None:
        pid = resolve_project_id(project_id)
        config_json = json.dumps(credentials)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO connector_credentials
                       (project_id, connector_name, display_name, config, status)
                   VALUES ($1, $2, $2, $3::jsonb, 'configured')
                   ON CONFLICT (project_id, connector_name)
                   DO UPDATE SET config = $3::jsonb,
                                 status = 'configured',
                                 updated_at = now()""",
                pid,
                connector_name,
                config_json,
            )

    async def delete(
        self, connector_name: str, *, project_id: str | None = None
    ) -> None:
        pid = resolve_project_id(project_id)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM connector_credentials "
                "WHERE project_id = $1 AND connector_name = $2",
                pid,
                connector_name,
            )

    async def list_all(
        self, *, project_id: str | None = None
    ) -> list[ConnectorStatus]:
        pid = resolve_project_id(project_id)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT connector_name, status, config "
                "FROM connector_credentials WHERE project_id = $1",
                pid,
            )
            return [
                ConnectorStatus(
                    connector_name=r["connector_name"],
                    status=r["status"] or "not_configured",
                    has_credentials=r["config"] is not None,
                )
                for r in rows
            ]


class PostgresOAuthTokenStore(OAuthTokenStore):
    """OAuth token store backed by the ``connector_credentials`` table."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get_token(
        self, connector_name: str, *, project_id: str | None = None
    ) -> TokenSet | None:
        pid = resolve_project_id(project_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT access_token, refresh_token, token_type, expires_at, scope
                   FROM connector_credentials
                   WHERE project_id = $1 AND connector_name = $2""",
                pid,
                connector_name,
            )
            if row and row["access_token"]:
                return TokenSet(
                    access_token=row["access_token"],
                    refresh_token=row["refresh_token"],
                    token_type=row["token_type"] or "Bearer",
                    expires_at=row["expires_at"],
                    scope=row["scope"],
                )
            return None

    async def save_token(
        self,
        connector_name: str,
        token_set: TokenSet,
        *,
        project_id: str | None = None,
    ) -> None:
        pid = resolve_project_id(project_id)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE connector_credentials
                   SET access_token = $3,
                       refresh_token = $4,
                       token_type = $5,
                       expires_at = $6,
                       scope = $7,
                       updated_at = now()
                   WHERE project_id = $1 AND connector_name = $2""",
                pid,
                connector_name,
                token_set.access_token,
                token_set.refresh_token,
                token_set.token_type,
                token_set.expires_at,
                token_set.scope,
            )

    async def needs_refresh(
        self, connector_name: str, *, project_id: str | None = None
    ) -> bool:
        token = await self.get_token(connector_name, project_id=project_id)
        if token is None:
            return True
        if token.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= token.expires_at


class PostgresCustomConnectorStore:
    """CRUD store for user-defined custom connectors in the ``custom_connectors`` table."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def save(
        self, spec: dict[str, Any], *, project_id: str | None = None
    ) -> None:
        """Insert or update a custom connector spec."""
        pid = resolve_project_id(project_id)
        auth_fields = json.dumps(spec.get("auth_fields", []))
        mcp_command = json.dumps(spec.get("mcp_command", []))
        oauth_scopes = json.dumps(spec.get("oauth_scopes", []))
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO custom_connectors
                       (project_id, name, display_name, category, description, auth_type,
                        auth_fields_json, mcp_command_json, docs_url,
                        agent_description, example_prompt,
                        oauth_authorize_url, oauth_token_url, oauth_scopes_json,
                        supports_webhook, supports_listener, supports_poller)
                   VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9,
                           $10, $11, $12, $13, $14::jsonb, $15, $16, $17)
                   ON CONFLICT (project_id, name)
                   DO UPDATE SET display_name = $3, category = $4, description = $5,
                                 auth_type = $6, auth_fields_json = $7::jsonb,
                                 mcp_command_json = $8::jsonb, docs_url = $9,
                                 agent_description = $10, example_prompt = $11,
                                 oauth_authorize_url = $12, oauth_token_url = $13,
                                 oauth_scopes_json = $14::jsonb,
                                 supports_webhook = $15, supports_listener = $16,
                                 supports_poller = $17, updated_at = now()""",
                pid,
                spec["name"],
                spec.get("display_name", spec["name"]),
                spec.get("category", "custom"),
                spec.get("description", ""),
                spec.get("auth_type", "api_key"),
                auth_fields,
                mcp_command,
                spec.get("docs_url"),
                spec.get("agent_description", ""),
                spec.get("example_prompt", ""),
                spec.get("oauth_authorize_url"),
                spec.get("oauth_token_url"),
                oauth_scopes,
                spec.get("supports_webhook", False),
                spec.get("supports_listener", False),
                spec.get("supports_poller", False),
            )

    async def get(
        self, name: str, *, project_id: str | None = None
    ) -> dict[str, Any] | None:
        pid = resolve_project_id(project_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM custom_connectors "
                "WHERE project_id = $1 AND name = $2",
                pid,
                name,
            )
            return self._row_to_dict(row) if row else None

    async def list_all(
        self, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        pid = resolve_project_id(project_id)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM custom_connectors "
                "WHERE project_id = $1 ORDER BY name",
                pid,
            )
            return [self._row_to_dict(r) for r in rows]

    async def delete(
        self, name: str, *, project_id: str | None = None
    ) -> None:
        pid = resolve_project_id(project_id)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM custom_connectors "
                "WHERE project_id = $1 AND name = $2",
                pid,
                name,
            )

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        auth_fields = row["auth_fields_json"]
        if isinstance(auth_fields, str):
            auth_fields = json.loads(auth_fields)
        mcp_command = row["mcp_command_json"]
        if isinstance(mcp_command, str):
            mcp_command = json.loads(mcp_command)
        oauth_scopes = row["oauth_scopes_json"]
        if isinstance(oauth_scopes, str):
            oauth_scopes = json.loads(oauth_scopes)
        return {
            "name": row["name"],
            "display_name": row["display_name"],
            "category": row["category"],
            "description": row["description"],
            "auth_type": row["auth_type"],
            "auth_fields": auth_fields or [],
            "mcp_command": mcp_command or [],
            "docs_url": row["docs_url"],
            "agent_description": row["agent_description"],
            "example_prompt": row["example_prompt"],
            "oauth_authorize_url": row["oauth_authorize_url"],
            "oauth_token_url": row["oauth_token_url"],
            "oauth_scopes": oauth_scopes or [],
            "supports_webhook": row["supports_webhook"],
            "supports_listener": row["supports_listener"],
            "supports_poller": row["supports_poller"],
        }


class PostgresCursorStore(CursorStore):
    """Cursor store backed by the ``connector_cursors`` table."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get(
        self, connector: str, channel: str, *, project_id: str | None = None
    ) -> str | None:
        pid = resolve_project_id(project_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT cursor_value FROM connector_cursors
                   WHERE project_id = $1 AND connector_name = $2 AND channel = $3""",
                pid,
                connector,
                channel,
            )
            return row["cursor_value"] if row else None

    async def set(
        self,
        connector: str,
        channel: str,
        cursor: str,
        *,
        project_id: str | None = None,
    ) -> None:
        pid = resolve_project_id(project_id)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO connector_cursors
                       (project_id, connector_name, channel, cursor_value)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (project_id, connector_name, channel)
                   DO UPDATE SET cursor_value = $4, updated_at = now()""",
                pid,
                connector,
                channel,
                cursor,
            )
