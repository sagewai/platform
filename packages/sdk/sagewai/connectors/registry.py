# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Connector registry — discovers, registers, and manages connectors."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING

from sagewai.connectors.auth import CredentialResolver
from sagewai.connectors.base import ConnectorSpec

if TYPE_CHECKING:
    from sagewai.mcp.client import McpConnection, ResilientMcpConnection
    from sagewai.connectors.stores import CredentialStore

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """Central registry for all connectors."""

    def __init__(self, credential_store: CredentialStore | None = None) -> None:
        self._connectors: dict[str, ConnectorSpec] = {}
        self._connections: dict[str, McpConnection | ResilientMcpConnection] = {}
        self._cached_credentials: dict[str, dict[str, str]] = {}
        self._custom_names: set[str] = set()
        self._resolver = CredentialResolver(store=credential_store)

    def register(self, connector: ConnectorSpec, *, custom: bool = False) -> None:
        """Register a connector."""
        self._connectors[connector.name] = connector
        if custom:
            self._custom_names.add(connector.name)

    def unregister(self, name: str) -> None:
        """Remove a connector from the registry."""
        self._connectors.pop(name, None)
        self._custom_names.discard(name)

    def get(self, name: str) -> ConnectorSpec:
        """Get connector by name. Raises KeyError if not found."""
        if name not in self._connectors:
            raise KeyError(f"Connector '{name}' not registered")
        return self._connectors[name]

    def list(self) -> list[ConnectorSpec]:
        """List all registered connectors."""
        return list(self._connectors.values())

    async def connect(
        self,
        name: str,
        credentials: dict[str, str] | None = None,
        *,
        resilient: bool = True,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> "McpConnection | ResilientMcpConnection":
        """Connect to a connector, resolving credentials if not provided.

        Args:
            name: Connector name.
            credentials: Credential dict; resolved from store/env if omitted.
            resilient: Wrap in ResilientMcpConnection for auto-reconnect.
            max_retries: Max reconnect attempts on transport failure.
            backoff_base: Base delay (seconds) for exponential backoff.
        """
        from sagewai.mcp.client import ResilientMcpConnection, _ProxiedTransport

        connector = self.get(name)
        if credentials is None:
            credentials = await self._resolver.resolve(connector)
        self._cached_credentials[name] = credentials

        if resilient:
            proxy = _ProxiedTransport()
            conn = await connector.connect(credentials, proxy=proxy)

            async def _reconnect() -> "McpConnection":
                new_proxy = _ProxiedTransport()
                return await connector.connect(credentials, proxy=new_proxy)

            wrapped = ResilientMcpConnection(
                inner=conn,
                reconnect_fn=_reconnect,
                max_retries=max_retries,
                backoff_base=backoff_base,
            )
            self._connections[name] = wrapped  # type: ignore[assignment]
            return wrapped
        else:
            conn = await connector.connect(credentials)
            self._connections[name] = conn
            return conn

    async def disconnect(self, name: str) -> None:
        """Disconnect and clean up a connector."""
        conn = self._connections.pop(name, None)
        if conn:
            await conn.close()

    async def disconnect_all(self) -> None:
        """Disconnect all active connections."""
        for name in list(self._connections):
            await self.disconnect(name)

    def catalog(self) -> list[dict]:
        """Return catalog for admin UI."""
        result = []
        for c in self._connectors.values():
            cred_status = c.name in self._connections
            # Check credential store for saved credentials
            if not cred_status and self._resolver._store:
                # Will be resolved async in the API layer; here we just check connections
                pass
            result.append({
                "name": c.name,
                "display_name": c.display_name,
                "category": c.category,
                "description": c.description,
                "auth_type": c.auth_type.value,
                "auth_fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "env_var": f.env_var,
                        "secret": f.secret,
                        "hint": f.hint,
                    }
                    for f in c.auth_fields
                ],
                "docs_url": c.docs_url,
                "agent_description": c.agent_description,
                "example_prompt": c.example_prompt,
                "supports_webhook": c.supports_webhook,
                "supports_listener": c.supports_listener,
                "supports_poller": c.supports_poller,
                "oauth_authorize_url": c.oauth_authorize_url,
                "oauth_token_url": c.oauth_token_url,
                "oauth_scopes": c.oauth_scopes or [],
                "connected": cred_status,
                "is_custom": c.name in self._custom_names,
            })
        return result

    def register_custom_from_dict(self, spec: dict) -> ConnectorSpec:
        """Build a ConnectorSpec from a dict and register it as custom."""
        from sagewai.connectors.base import AuthField, AuthType

        auth_fields = [
            AuthField(**f) for f in spec.get("auth_fields", [])
        ]
        connector = ConnectorSpec(
            name=spec["name"],
            display_name=spec.get("display_name", spec["name"]),
            category=spec.get("category", "custom"),
            description=spec.get("description", ""),
            auth_type=AuthType(spec.get("auth_type", "api_key")),
            auth_fields=auth_fields,
            mcp_command=spec.get("mcp_command", []),
            docs_url=spec.get("docs_url"),
            agent_description=spec.get("agent_description", ""),
            example_prompt=spec.get("example_prompt", ""),
            oauth_authorize_url=spec.get("oauth_authorize_url"),
            oauth_token_url=spec.get("oauth_token_url"),
            oauth_scopes=spec.get("oauth_scopes"),
            supports_webhook=spec.get("supports_webhook", False),
            supports_listener=spec.get("supports_listener", False),
            supports_poller=spec.get("supports_poller", False),
        )
        self.register(connector, custom=True)
        return connector

    def discover_builtins(self) -> None:
        """Auto-discover connectors from sagewai.connectors.builtins."""
        try:
            from sagewai.connectors import builtins as builtins_pkg

            for importer, modname, ispkg in pkgutil.iter_modules(builtins_pkg.__path__):
                try:
                    mod = importlib.import_module(
                        f"sagewai.connectors.builtins.{modname}",
                    )
                    for attr_name in dir(mod):
                        attr = getattr(mod, attr_name)
                        if (
                            isinstance(attr, type)
                            and issubclass(attr, ConnectorSpec)
                            and attr is not ConnectorSpec
                        ):
                            try:
                                instance = attr()
                                self.register(instance)
                            except Exception:
                                logger.warning("Failed to instantiate %s", attr_name)
                except Exception:
                    logger.warning("Failed to import builtin connector %s", modname)
        except ImportError:
            logger.debug("No builtins package found")
