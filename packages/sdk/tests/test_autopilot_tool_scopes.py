# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for tool_scopes — Plan K Task 1."""

from __future__ import annotations

import pytest

from sagewai.autopilot.tool_scopes import get_scopes, scopes_for_tools


# ── get_scopes ─────────────────────────────────────────────────────────────


def test_known_tool_returns_nonempty_scopes():
    scopes = get_scopes("web_search")
    assert len(scopes) > 0


def test_known_trusted_tool_returns_nonempty_scopes():
    scopes = get_scopes("read_file")
    assert len(scopes) > 0


def test_unknown_tool_returns_empty_set():
    """Unknown tools return an empty scope set — no Sealed profile needed."""
    scopes = get_scopes("totally_unknown_xyz_tool_999")
    assert scopes == set()


def test_scopes_are_strings():
    scopes = get_scopes("web_search")
    assert all(isinstance(s, str) for s in scopes)


# ── scopes_for_tools ──────────────────────────────────────────────────────


def test_scopes_for_empty_list_returns_empty():
    assert scopes_for_tools([]) == set()


def test_scopes_for_single_tool():
    result = scopes_for_tools(["web_search"])
    assert result == get_scopes("web_search")


def test_scopes_for_multiple_tools_union():
    web = get_scopes("web_search")
    read = get_scopes("read_file")
    combined = scopes_for_tools(["web_search", "read_file"])
    assert combined == web | read


def test_unknown_tools_contribute_no_scopes():
    result = scopes_for_tools(["read_file", "totally_unknown_xyz"])
    assert result == get_scopes("read_file")


def test_scopes_are_frozenset_or_set():
    result = scopes_for_tools(["web_search"])
    assert isinstance(result, (set, frozenset))
