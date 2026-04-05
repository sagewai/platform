# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Base models for the connector framework."""

from __future__ import annotations

import os
import time
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from sagewai.mcp.client import McpClient, McpConnection, _ProxiedTransport


class AuthType(str, Enum):
    """Authentication type for a connector."""

    ENV_KEY = "env_key"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    NONE = "none"


class AuthField(BaseModel):
    """A single credential field for a connector."""

    key: str
    label: str
    env_var: str
    secret: bool = True
    hint: str = ""


class HealthStatus(BaseModel):
    """Health check result for a connector."""

    status: Literal["healthy", "degraded", "disconnected"]
    latency_ms: int | None = None
    tool_count: int | None = None
    error: str | None = None
    last_check: str | None = None


class TokenSet(BaseModel):
    """OAuth2 token set with refresh tracking."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    scope: str | None = None


class ConnectorStatus(BaseModel):
    """Status of a configured connector."""

    connector_name: str
    status: Literal["configured", "via_env", "not_configured"]
    has_credentials: bool
    env_vars_set: dict[str, bool] = Field(default_factory=dict)


class ConnectorSpec(BaseModel):
    """Base class for all connectors.

    Uses Pydantic BaseModel to enforce required fields at instantiation.
    Methods have default implementations for standard MCP-subprocess connectors.
    Override connect() or health_check() for custom behavior.
    """

    name: str
    display_name: str
    category: str
    description: str
    auth_type: AuthType
    auth_fields: list[AuthField]
    mcp_command: list[str]
    docs_url: str | None = None
    agent_description: str = ""
    example_prompt: str = ""

    # OAuth2-specific
    oauth_authorize_url: str | None = None
    oauth_token_url: str | None = None
    oauth_scopes: list[str] | None = None

    # Event support
    supports_webhook: bool = False
    supports_listener: bool = False
    supports_poller: bool = False

    model_config = {"arbitrary_types_allowed": True}

    def _build_env(self, credentials: dict[str, str]) -> dict[str, str]:
        """Build environment dict with credentials mapped to env vars."""
        env = dict(os.environ)
        for field in self.auth_fields:
            if field.key in credentials:
                env[field.env_var] = credentials[field.key]
        return env

    async def connect(
        self,
        credentials: dict[str, str],
        proxy: _ProxiedTransport | None = None,
    ) -> McpConnection:
        """Connect via MCP subprocess with credentials as env vars.

        Args:
            credentials: Credential key-value pairs.
            proxy: Optional proxy transport for resilient connections.
                   When provided, tool handler closures bind to the proxy
                   so they survive subprocess restarts.
        """
        env = self._build_env(credentials)
        return await McpClient.connect_managed(self.mcp_command, env=env, proxy=proxy)

    async def health_check(self, credentials: dict[str, str]) -> HealthStatus:
        """Check health by connecting, listing tools, and disconnecting."""
        start = time.monotonic()
        try:
            conn = await self.connect(credentials)
            tool_count = len(conn.tools)
            await conn.close()
            elapsed = int((time.monotonic() - start) * 1000)
            return HealthStatus(
                status="healthy",
                latency_ms=elapsed,
                tool_count=tool_count,
            )
        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return HealthStatus(
                status="disconnected",
                latency_ms=elapsed,
                error=str(e),
            )

    def validate_credentials(self, credentials: dict[str, str]) -> list[str]:
        """Return list of validation errors (empty = valid)."""
        errors = []
        for field in self.auth_fields:
            if field.key not in credentials or not credentials[field.key]:
                errors.append(f"Missing required field: {field.label}")
        return errors

    async def verify_webhook(
        self,
        request_body: bytes,
        headers: dict[str, str],
        credentials: dict[str, str],
    ) -> bool:
        """Verify webhook signature. Default: reject all (fail-closed)."""
        return False
