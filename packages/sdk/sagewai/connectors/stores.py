# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Credential, OAuth token, and cursor store interfaces + in-memory implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from sagewai.connectors.base import ConnectorStatus, TokenSet


class CredentialStore(ABC):
    """Pluggable backend for connector credentials."""

    @abstractmethod
    async def get(self, connector_name: str) -> dict[str, str] | None: ...

    @abstractmethod
    async def put(self, connector_name: str, credentials: dict[str, str]) -> None: ...

    @abstractmethod
    async def delete(self, connector_name: str) -> None: ...

    @abstractmethod
    async def list_all(self) -> list[ConnectorStatus]: ...


class OAuthTokenStore(ABC):
    """Stores OAuth2 tokens with refresh tracking."""

    @abstractmethod
    async def get_token(self, connector_name: str) -> TokenSet | None: ...

    @abstractmethod
    async def save_token(self, connector_name: str, token_set: TokenSet) -> None: ...

    @abstractmethod
    async def needs_refresh(self, connector_name: str) -> bool: ...


class CursorStore(ABC):
    """Stores polling cursors (last-seen markers) per connector+channel."""

    @abstractmethod
    async def get(self, connector: str, channel: str) -> str | None: ...

    @abstractmethod
    async def set(self, connector: str, channel: str, cursor: str) -> None: ...


# ── In-memory implementations ──


class InMemoryCredentialStore(CredentialStore):
    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {}

    async def get(self, connector_name: str) -> dict[str, str] | None:
        return self._data.get(connector_name)

    async def put(self, connector_name: str, credentials: dict[str, str]) -> None:
        self._data[connector_name] = credentials

    async def delete(self, connector_name: str) -> None:
        self._data.pop(connector_name, None)

    async def list_all(self) -> list[ConnectorStatus]:
        return [
            ConnectorStatus(
                connector_name=name,
                status="configured",
                has_credentials=True,
            )
            for name in self._data
        ]


class InMemoryOAuthTokenStore(OAuthTokenStore):
    def __init__(self) -> None:
        self._tokens: dict[str, TokenSet] = {}

    async def get_token(self, connector_name: str) -> TokenSet | None:
        return self._tokens.get(connector_name)

    async def save_token(self, connector_name: str, token_set: TokenSet) -> None:
        self._tokens[connector_name] = token_set

    async def needs_refresh(self, connector_name: str) -> bool:
        token = self._tokens.get(connector_name)
        if token is None:
            return True
        if token.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= token.expires_at


class InMemoryCursorStore(CursorStore):
    def __init__(self) -> None:
        self._cursors: dict[str, str] = {}

    async def get(self, connector: str, channel: str) -> str | None:
        return self._cursors.get(f"{connector}:{channel}")

    async def set(self, connector: str, channel: str, cursor: str) -> None:
        self._cursors[f"{connector}:{channel}"] = cursor
