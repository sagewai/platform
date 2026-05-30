# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Protocol-agnostic errors for the subscription foundation.

Note: oversized / overflow / global-pressure are NON-exceptional — they
are ``EmitResult`` values + cumulative counters surfaced in
``DrainResult``, never raised. Only subscribe-time limit and lookup-time
not-found are exceptions.
"""
from __future__ import annotations

from typing import ClassVar


class SubscriptionError(Exception):
    """Base for subscription-foundation errors."""

    code: ClassVar[str] = "subscription_error"


class SubscriptionLimitExceededError(SubscriptionError):
    """``subscribe()`` called past ``max_active_subscriptions``."""

    code: ClassVar[str] = "subscription_limit_exceeded"

    def __init__(self, *, limit: int) -> None:
        self.limit = limit
        super().__init__(
            f"max active subscriptions ({limit}) reached; unsubscribe before adding more"
        )


class SubscriptionNotFoundError(SubscriptionError):
    """``drain()`` / ``unsubscribe()`` for an unknown or reaped id."""

    code: ClassVar[str] = "subscription_not_found"

    def __init__(self, *, subscription_id: str) -> None:
        self.subscription_id = subscription_id
        super().__init__(f"subscription {subscription_id!r} not found (unknown or reaped)")


__all__ = [
    "SubscriptionError",
    "SubscriptionLimitExceededError",
    "SubscriptionNotFoundError",
]
