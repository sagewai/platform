# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Subscription-foundation error hierarchy tests."""
from __future__ import annotations

from sagewai.connections.subscriptions.errors import (
    SubscriptionError,
    SubscriptionLimitExceededError,
    SubscriptionNotFoundError,
)


def test_error_hierarchy():
    assert issubclass(SubscriptionLimitExceededError, SubscriptionError)
    assert issubclass(SubscriptionNotFoundError, SubscriptionError)


def test_error_codes_stable():
    assert SubscriptionError.code == "subscription_error"
    assert SubscriptionLimitExceededError.code == "subscription_limit_exceeded"
    assert SubscriptionNotFoundError.code == "subscription_not_found"


def test_limit_exceeded_carries_limit():
    err = SubscriptionLimitExceededError(limit=64)
    assert err.limit == 64
    assert "64" in str(err)


def test_not_found_carries_id():
    err = SubscriptionNotFoundError(subscription_id="sub-abc")
    assert err.subscription_id == "sub-abc"
    assert "sub-abc" in str(err)
