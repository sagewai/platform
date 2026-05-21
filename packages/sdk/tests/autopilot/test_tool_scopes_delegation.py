# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Verify scopes_for_tools delegates to the tool catalog with legacy fallback."""
from sagewai.autopilot import tool_scopes


def test_catalogued_tool_returns_catalog_scopes():
    # github is in the catalog with scopes:
    # network.outbound.fetch + secrets.github_token + git.write
    # (git.write added in batch 2b when write ops landed)
    assert tool_scopes.scopes_for_tools(["github"]) == frozenset(
        {"network.outbound.fetch", "secrets.github_token", "git.write"}
    )


def test_legacy_only_tool_still_returns_legacy_scopes():
    # send_email is in the legacy dict but not (yet) in the catalog
    out = tool_scopes.scopes_for_tools(["send_email"])
    assert "secrets.email_api_key" in out


def test_unknown_tool_returns_empty():
    assert tool_scopes.scopes_for_tools(["completely_made_up_tool"]) == frozenset()


def test_mixed_catalogued_and_legacy_union():
    out = tool_scopes.scopes_for_tools(["github", "send_email"])
    # Catalog contribution
    assert "secrets.github_token" in out
    # Legacy contribution
    assert "secrets.email_api_key" in out
