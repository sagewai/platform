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

PR1 ships the foundation layer: ``Connection`` dataclass envelope and
``ConnectionStore`` with generic CRUD. Subsequent PRs add protocol
plugins (PR2), credentials backends (PR3), admin routes + CLI (PR4),
and the unified admin UI (PR5).
"""
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
from sagewai.connections.store import ConnectionStore, DefaultKeyExtractor

__all__ = [
    "Connection",
    "ConnectionError",
    "ConnectionNotFoundError",
    "ConnectionStatus",
    "ConnectionStore",
    "DefaultKeyExtractor",
    "DuplicateDisplayNameError",
    "HealthResult",
    "StoreCorruptedError",
    "TestResult",
    "UnknownProtocolError",
    "UnsupportedStoreVersionError",
    "valid_protocol_ids",
]
