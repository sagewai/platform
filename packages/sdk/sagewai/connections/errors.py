# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Connection store error hierarchy.

Each exception carries a stable ``code`` class attribute consumed by the
admin UI to render actionable links and by mission logs for structured
error identification. Mirrors the OAuth error-hierarchy pattern shipped
in PR #356.
"""
from __future__ import annotations


class ConnectionError(Exception):
    """Base class for all connection-store failures."""

    code = "connection_error"


class ConnectionNotFoundError(ConnectionError):
    """Lookup by id returned no record."""

    code = "connection_not_found"


class DuplicateDisplayNameError(ConnectionError):
    """A connection with the same (project_id, protocol, display_name) already exists."""

    code = "connection_duplicate_display_name"


class IdCollisionError(ConnectionError):
    """A create() call with an explicit id_override hit an existing id."""

    code = "id_collision"

    def __init__(self, connection_id: str) -> None:
        self.connection_id = connection_id
        super().__init__(f"connection id {connection_id!r} already exists")


class StoreCorruptedError(ConnectionError):
    """The connections.json file is unreadable or malformed."""

    code = "connection_store_corrupted"


class UnknownProtocolError(ConnectionError):
    """Protocol id is not in the configured allowed-protocols set."""

    code = "connection_unknown_protocol"


class UnsupportedStoreVersionError(ConnectionError):
    """connections.json declares a version this build does not support."""

    code = "connection_store_unsupported_version"


__all__ = [
    "ConnectionError",
    "ConnectionNotFoundError",
    "DuplicateDisplayNameError",
    "IdCollisionError",
    "StoreCorruptedError",
    "UnknownProtocolError",
    "UnsupportedStoreVersionError",
]
