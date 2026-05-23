# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""In-process pending-auth state store.

Holds CSRF state token → PKCE verifier + oauth_client_id mapping during
the brief window between the operator clicking Authorize and the vendor
callback arriving. Single-use (pop deletes); 10-minute TTL.

Multi-instance admin deployments swap this for Redis following the same
pattern as Sealed-iii.A (out of scope for batch 3 kickoff).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingAuthEntry:
    oauth_client_id: str
    code_verifier: str
    redirect_uri: str


class PendingAuthStore:
    """Thread-safe in-process state map with single-use TTL semantics."""

    def __init__(self, *, ttl_seconds: int = 600) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, PendingAuthEntry]] = {}

    def put(self, state: str, entry: PendingAuthEntry) -> None:
        with self._lock:
            self._entries[state] = (time.time(), entry)

    def pop(self, state: str) -> PendingAuthEntry | None:
        with self._lock:
            row = self._entries.pop(state, None)
            if row is None:
                return None
            created_at, entry = row
            if time.time() - created_at > self._ttl:
                return None
            return entry


_default: PendingAuthStore | None = None


def get_default_store() -> PendingAuthStore:
    """Return the process-wide default store (lazy-initialized)."""
    global _default
    if _default is None:
        _default = PendingAuthStore()
    return _default


def reset_default_store_for_tests() -> None:
    """Test hook — clears the singleton so each test gets a fresh store."""
    global _default
    _default = None


__all__ = [
    "PendingAuthEntry",
    "PendingAuthStore",
    "get_default_store",
    "reset_default_store_for_tests",
]
