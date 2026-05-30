# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SubscriptionManager process-singleton accessor tests."""
from __future__ import annotations

import pytest

from sagewai.connections.subscriptions.manager import (
    SubscriptionManager,
    get_subscription_manager,
    set_subscription_manager,
)


def test_set_and_get_roundtrip():
    mgr = SubscriptionManager()
    set_subscription_manager(mgr)
    assert get_subscription_manager() is mgr
    set_subscription_manager(None)


def test_get_before_set_raises():
    set_subscription_manager(None)
    with pytest.raises(RuntimeError, match="not initialized"):
        get_subscription_manager()
