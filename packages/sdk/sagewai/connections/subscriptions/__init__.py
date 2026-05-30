# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Async subscription foundation — poll-a-buffer streaming for connections."""
from __future__ import annotations

from sagewai.connections.subscriptions.base import (
    DrainResult,
    EmitResult,
    SubscriptionPlugin,
    SubscriptionStats,
)
from sagewai.connections.subscriptions.errors import (
    SubscriptionError,
    SubscriptionLimitExceededError,
    SubscriptionNotFoundError,
)
from sagewai.connections.subscriptions.manager import (
    SubscriptionManager,
    get_subscription_manager,
    set_subscription_manager,
)

__all__ = [
    "DrainResult",
    "EmitResult",
    "SubscriptionError",
    "SubscriptionLimitExceededError",
    "SubscriptionManager",
    "SubscriptionNotFoundError",
    "SubscriptionPlugin",
    "SubscriptionStats",
    "get_subscription_manager",
    "set_subscription_manager",
]
