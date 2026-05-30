# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SubscriptionManager admin-lifespan wiring tests."""
from __future__ import annotations

import pytest

from sagewai.connections.subscriptions.manager import (
    SubscriptionManager,
    get_subscription_manager,
    set_subscription_manager,
)


def test_build_subscription_manager_returns_manager():
    from sagewai.admin.serve import _build_subscription_manager

    mgr = _build_subscription_manager()
    assert isinstance(mgr, SubscriptionManager)


@pytest.mark.asyncio
async def test_lifespan_sets_and_clears_manager():
    """The helper the lifespan calls must set the singleton on enter and
    aclose+clear on exit."""
    from sagewai.admin.serve import _build_subscription_manager

    mgr = _build_subscription_manager()
    assert isinstance(mgr, SubscriptionManager)
    set_subscription_manager(mgr)
    mgr.start_reaper()
    assert get_subscription_manager() is mgr
    # teardown
    await mgr.aclose()
    set_subscription_manager(None)
    with pytest.raises(RuntimeError):
        get_subscription_manager()


@pytest.mark.asyncio
async def test_admin_app_lifespan_wires_manager():
    """Driving the real FastAPI lifespan must set + clear the singleton."""
    import tempfile
    from pathlib import Path

    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile

    set_subscription_manager(None)
    with tempfile.TemporaryDirectory() as td:
        sf = AdminStateFile(str(Path(td) / "admin-state.json"))
        app = create_admin_serve_app(sf)
        async with app.router.lifespan_context(app):
            mgr = get_subscription_manager()
            assert isinstance(mgr, SubscriptionManager)
            assert getattr(app.state, "subscription_manager", None) is mgr
        # after exit the singleton is cleared
        with pytest.raises(RuntimeError):
            get_subscription_manager()
