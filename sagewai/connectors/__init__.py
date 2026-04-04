# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""
Connector framework for sagewai.

Provides ConnectorSpec, ConnectorRegistry, credential resolution,
health monitoring, stores, and builtin connectors.
"""

from sagewai.connectors.base import (
    AuthField,
    AuthType,
    ConnectorSpec,
    ConnectorStatus,
    HealthStatus,
    TokenSet,
)
from sagewai.connectors.auth import CredentialResolver
from sagewai.connectors.health import HealthMonitor
from sagewai.connectors.registry import ConnectorRegistry
from sagewai.connectors.stores import (
    CredentialStore,
    CursorStore,
    InMemoryCredentialStore,
    InMemoryCursorStore,
    InMemoryOAuthTokenStore,
    OAuthTokenStore,
)

try:
    from sagewai.connectors.pg_stores import (
        PostgresCredentialStore,
        PostgresCursorStore,
        PostgresOAuthTokenStore,
    )
except ImportError:
    pass  # asyncpg not installed

__all__ = [
    "AuthField",
    "AuthType",
    "ConnectorSpec",
    "ConnectorStatus",
    "CredentialResolver",
    "CredentialStore",
    "ConnectorRegistry",
    "CursorStore",
    "HealthMonitor",
    "HealthStatus",
    "InMemoryCredentialStore",
    "InMemoryCursorStore",
    "InMemoryOAuthTokenStore",
    "OAuthTokenStore",
    "PostgresCredentialStore",
    "PostgresCursorStore",
    "PostgresOAuthTokenStore",
    "TokenSet",
]
