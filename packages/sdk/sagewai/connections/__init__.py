# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Connections Platform — unified external-dependencies model.

PR1 shipped the foundation layer: ``Connection`` dataclass envelope and
``ConnectionStore`` with generic CRUD.

PR2 ships the protocol plugin layer: ``ProtocolPlugin`` contract,
``PluginContext`` service-locator, and 5 plugins (http, oauth2, mcp,
inference, sdk). Plugins are registered but not yet mounted on admin
routes / CLI (PR4 does that). Legacy admin/CLI surfaces continue to
serve traffic.

Subsequent PRs: PR3 (credentials backends — local/env/sops), PR4
(generic CRUD admin routes + CLI, removes legacy per-kind routes), PR5
(admin UI rewrite + examples update + docs).
"""
from sagewai.connections.bootstrap import (
    ConnectionsContext,
    build_connections_context,
)
from sagewai.connections.credentials import (
    BACKENDS as CREDENTIALS_BACKENDS,
    BackendUnhealthyError,
    CredentialsBackend,
    CredentialsBackendRouter,
    CredentialsError,
    DopplerApiError,
    DopplerAuthError,
    DopplerBackend,
    DopplerBackendConfig,
    DopplerConfigError,
    DopplerError,
    InvalidBackendConfigError,
    MissingEnvVarError,
    SopsDecryptError,
    UnknownBackendError as UnknownCredentialsBackendError,
    VaultAuthError,
    VaultBackend,
    VaultBackendConfig,
    VaultConfigError,
    VaultError,
    VaultReadError,
)
from sagewai.connections.errors import (
    ConnectionError,
    ConnectionNotFoundError,
    DuplicateDisplayNameError,
    StoreCorruptedError,
    UnknownProtocolError,
    UnsupportedStoreVersionError,
)
from sagewai.connections.models import (
    Connection,
    ConnectionStatus,
    HealthResult,
    TestResult,
    valid_protocol_ids,
)
from sagewai.connections.protocols import (
    DEFAULT_KEY_FOR,
    PROTOCOLS,
    PluginContext,
    ProtocolPlugin,
    all_protocols,
    get_protocol,
)
from sagewai.connections.store import ConnectionStore, DefaultKeyExtractor

__all__ = [
    "BackendUnhealthyError",
    "CREDENTIALS_BACKENDS",
    "Connection",
    "ConnectionError",
    "ConnectionNotFoundError",
    "ConnectionStatus",
    "ConnectionStore",
    "ConnectionsContext",
    "CredentialsBackend",
    "CredentialsBackendRouter",
    "CredentialsError",
    "DEFAULT_KEY_FOR",
    "DefaultKeyExtractor",
    "DopplerApiError",
    "DopplerAuthError",
    "DopplerBackend",
    "DopplerBackendConfig",
    "DopplerConfigError",
    "DopplerError",
    "DuplicateDisplayNameError",
    "HealthResult",
    "InvalidBackendConfigError",
    "MissingEnvVarError",
    "PROTOCOLS",
    "PluginContext",
    "ProtocolPlugin",
    "SopsDecryptError",
    "StoreCorruptedError",
    "TestResult",
    "UnknownCredentialsBackendError",
    "UnknownProtocolError",
    "UnsupportedStoreVersionError",
    "VaultAuthError",
    "VaultBackend",
    "VaultBackendConfig",
    "VaultConfigError",
    "VaultError",
    "VaultReadError",
    "all_protocols",
    "build_connections_context",
    "get_protocol",
    "valid_protocol_ids",
]
