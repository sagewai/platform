# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for tool_risk_profile — Plan J Task 1."""

from __future__ import annotations

import pytest

from sagewai.autopilot.tool_risk_profile import (
    SandboxTier,
    get_tier,
    is_downgrade,
    tier_for_tools,
)


# ── SandboxTier ordering ──────────────────────────────────────────────────


def test_trusted_is_least_restrictive():
    assert SandboxTier.TRUSTED < SandboxTier.SANDBOXED < SandboxTier.UNTRUSTED


def test_is_downgrade_same_tier():
    assert is_downgrade(SandboxTier.SANDBOXED, SandboxTier.SANDBOXED) is False


def test_is_downgrade_to_less_restrictive():
    assert is_downgrade(SandboxTier.UNTRUSTED, SandboxTier.SANDBOXED) is True


def test_is_downgrade_to_more_restrictive():
    assert is_downgrade(SandboxTier.SANDBOXED, SandboxTier.UNTRUSTED) is False


def test_is_downgrade_trusted_to_untrusted():
    assert is_downgrade(SandboxTier.UNTRUSTED, SandboxTier.TRUSTED) is True


# ── get_tier ──────────────────────────────────────────────────────────────


def test_get_tier_known_trusted_tool():
    assert get_tier("read_file") == SandboxTier.TRUSTED


def test_get_tier_known_sandboxed_tool():
    assert get_tier("web_search") == SandboxTier.SANDBOXED


def test_get_tier_known_untrusted_tool():
    assert get_tier("shell_exec") == SandboxTier.UNTRUSTED


def test_get_tier_unknown_tool_defaults_untrusted():
    """Fail-secure: unknown tools must default to UNTRUSTED."""
    assert get_tier("totally_unknown_xyz_tool_123") == SandboxTier.UNTRUSTED


# ── tier_for_tools ────────────────────────────────────────────────────────


def test_tier_for_tools_empty_returns_trusted():
    assert tier_for_tools([]) == SandboxTier.TRUSTED


def test_tier_for_tools_all_trusted():
    assert tier_for_tools(["read_file", "list_dir"]) == SandboxTier.TRUSTED


def test_tier_for_tools_mixed_returns_max():
    assert tier_for_tools(["read_file", "web_search"]) == SandboxTier.SANDBOXED


def test_tier_for_tools_with_untrusted_returns_untrusted():
    assert tier_for_tools(["read_file", "shell_exec"]) == SandboxTier.UNTRUSTED


def test_tier_for_tools_unknown_pulls_to_untrusted():
    assert tier_for_tools(["read_file", "mystery_tool"]) == SandboxTier.UNTRUSTED
