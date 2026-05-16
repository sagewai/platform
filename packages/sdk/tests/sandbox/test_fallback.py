# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for fallback behavior when the requested backend is unhealthy."""
import pytest

from sagewai.sandbox.fallback import apply_fallback
from sagewai.sandbox.models import BackendHealth, SandboxMode


def test_fallback_per_run_to_per_tool_when_unhealthy():
    health = BackendHealth(ok=False, backend="docker", detail="daemon unreachable")
    new_mode = apply_fallback(
        requested=SandboxMode.PER_RUN, health=health, production=False
    )
    assert new_mode is SandboxMode.PER_TOOL


def test_fallback_per_tool_to_none_when_unhealthy():
    health = BackendHealth(ok=False, backend="docker", detail="x")
    assert apply_fallback(SandboxMode.PER_TOOL, health, production=False) is SandboxMode.NONE


def test_fallback_none_stays_none():
    health = BackendHealth(ok=False, backend="null", detail="x")
    assert apply_fallback(SandboxMode.NONE, health, production=False) is SandboxMode.NONE


def test_fallback_production_refuses_to_downgrade():
    health = BackendHealth(ok=False, backend="docker", detail="daemon unreachable")
    with pytest.raises(RuntimeError) as ei:
        apply_fallback(SandboxMode.PER_RUN, health, production=True)
    assert "production" in str(ei.value)


def test_fallback_healthy_returns_requested():
    health = BackendHealth(ok=True, backend="docker", detail="ok")
    assert apply_fallback(SandboxMode.PER_RUN, health, production=True) is SandboxMode.PER_RUN
