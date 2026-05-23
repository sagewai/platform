# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Batch 3 catalog load smoke test.

Asserts the spotify + gmail YAMLs round-trip through ``registry.load()``,
their setup.oauth_provider matches exec.http.auth.oauth_provider, and
the full catalog (48 prior entries + 2 new = 50) still loads.
"""
from __future__ import annotations

from sagewai.tools import registry


def test_batch3_catalog_loads():
    registry._reset()
    registry.load()
    # Both new entries present
    assert "spotify" in registry._entries
    assert "gmail" in registry._entries
    # Setup vs exec oauth_provider equality enforced by registry post-validation
    sp = registry._entries["spotify"]
    gm = registry._entries["gmail"]
    assert sp.setup["auth_complexity"] == "oauth2"
    assert gm.setup["auth_complexity"] == "oauth2"
    assert sp.setup["oauth_provider"] == sp.exec_["http"]["auth"]["oauth_provider"] == "spotify"
    assert gm.setup["oauth_provider"] == gm.exec_["http"]["auth"]["oauth_provider"] == "google"
    # Required scopes declared
    assert len(sp.setup["required_scopes"]) >= 1
    assert len(gm.setup["required_scopes"]) >= 1
    # Whole catalog clean (no regression on the 48 prior entries)
    assert len(registry._entries) >= 50
