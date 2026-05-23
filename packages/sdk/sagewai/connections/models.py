# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Connection record envelope + supporting result types.

The :class:`Connection` dataclass is the generic, plugin-agnostic record
shape persisted by :class:`sagewai.connections.store.ConnectionStore`.
Plugins (PR2) own ``protocol_data`` — the store treats it as opaque
``dict[str, Any]``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ConnectionStatus = Literal["ready", "pending", "expired", "revoked", "error"]


_DEFAULT_PROTOCOL_IDS: tuple[str, ...] = ("http", "oauth2", "mcp", "inference", "sdk")


def valid_protocol_ids() -> tuple[str, ...]:
    """Return the default allowed-protocols tuple.

    PR1 uses this as the ``ConnectionStore`` default. PR2 swaps it for
    ``tuple(p.id for p in PROTOCOLS)`` from the plugin registry.
    """
    return _DEFAULT_PROTOCOL_IDS


@dataclass(frozen=True, slots=True)
class Connection:
    """Generic envelope every connection shares.

    Immutable by design — callers receive new instances after updates.
    The store enforces invariants (display-name uniqueness, default-flag
    uniqueness within the (project, protocol, default_key) tuple);
    plugins (PR2) validate the ``protocol_data`` payload.
    """

    id: str
    protocol: str
    project_id: str | None
    display_name: str
    tags: tuple[str, ...]
    credentials_backend: dict[str, Any] | None
    status: ConnectionStatus
    last_tested_at: str | None
    last_test_ok: bool | None
    is_default: bool
    created_at: str
    updated_at: str
    last_error: dict[str, Any] | None
    protocol_data: dict[str, Any]

    # Discriminator is fixed for the single record kind in the store.
    kind: str = "connection"


@dataclass(frozen=True, slots=True)
class TestResult:
    """Outcome of a plugin's ``test()`` call against a live connection."""

    ok: bool
    status_code: int | None = None
    message: str | None = None

    # pytest sentinel: don't collect this dataclass as a test class.
    __test__ = False


@dataclass(frozen=True, slots=True)
class HealthResult:
    """Outcome of a credentials-backend ``health()`` self-check (PR3)."""

    ok: bool
    message: str | None = None


__all__ = [
    "Connection",
    "ConnectionStatus",
    "HealthResult",
    "TestResult",
    "valid_protocol_ids",
]
