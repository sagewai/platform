# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.

"""Tests for the Sealed identity-execution preview gate (Modes 2/3/3b).

Mirrors the host-exec policy: single-org runs identity modes at the
operator's own risk (always allowed); multi-tenant mode refuses them
unless ``SAGEWAI_SEALED_PREVIEW`` is explicitly set, because the Sealed
*runtime* protections (live injection, RPC-boundary redaction, per-key
ACL, hard-revoke) are experimental and not wired into the worker path.
"""
from __future__ import annotations

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.policy import (
    identity_execution_allowed,
    is_identity_execution_mode,
)


@pytest.mark.parametrize("mode", ["identity", "full", "full_jit"])
def test_is_identity_mode_true_for_identity_modes(mode):
    assert is_identity_execution_mode(mode) is True


@pytest.mark.parametrize("mode", ["bare", "sandboxed"])
def test_is_identity_mode_false_for_non_identity_modes(mode):
    assert is_identity_execution_mode(mode) is False


def test_is_identity_mode_accepts_enum_members():
    assert is_identity_execution_mode(ExecutionMode.IDENTITY) is True
    assert is_identity_execution_mode(ExecutionMode.FULL) is True
    assert is_identity_execution_mode(ExecutionMode.FULL_JIT) is True
    assert is_identity_execution_mode(ExecutionMode.BARE) is False
    assert is_identity_execution_mode(ExecutionMode.SANDBOXED) is False


def test_is_identity_mode_false_for_unknown_value():
    assert is_identity_execution_mode("banana") is False


def test_identity_execution_allowed_single_org(monkeypatch):
    """Single-org operator runs identity modes at their own risk → allowed."""
    monkeypatch.delenv("SAGEWAI_TENANCY_MODE", raising=False)
    monkeypatch.delenv("SAGEWAI_SEALED_PREVIEW", raising=False)
    assert identity_execution_allowed() is True


def test_identity_execution_refused_multi_without_optin(monkeypatch):
    """Multi-tenant + no opt-in → preview gate refuses identity modes."""
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.delenv("SAGEWAI_SEALED_PREVIEW", raising=False)
    assert identity_execution_allowed() is False


@pytest.mark.parametrize("flag", ["1", "true"])
def test_identity_execution_allowed_multi_with_optin(monkeypatch, flag):
    """Multi-tenant + explicit opt-in → identity modes permitted."""
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_SEALED_PREVIEW", flag)
    assert identity_execution_allowed() is True


def test_identity_execution_optin_ignored_for_garbage_value(monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_SEALED_PREVIEW", "maybe")
    assert identity_execution_allowed() is False
