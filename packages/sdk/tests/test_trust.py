# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for trust-gated agent initialization."""

from __future__ import annotations

import pytest

from sagewai.core.trust import DeferredInit, DeferredInitResult, TrustLevel


# ------------------------------------------------------------------
# TrustLevel enum
# ------------------------------------------------------------------


def test_trust_level_ordering():
    assert TrustLevel.UNTRUSTED < TrustLevel.SANDBOXED < TrustLevel.TRUSTED


def test_trust_level_values():
    assert TrustLevel.UNTRUSTED == 0
    assert TrustLevel.SANDBOXED == 1
    assert TrustLevel.TRUSTED == 2


# ------------------------------------------------------------------
# Default state
# ------------------------------------------------------------------


def test_default_state():
    init = DeferredInit()
    assert init.trust_level == TrustLevel.UNTRUSTED
    assert not init.is_pre_trust_complete
    assert not init.is_post_trust_complete
    assert not init.is_fully_initialized
    assert init.init_result is None


# ------------------------------------------------------------------
# Pre-trust completion
# ------------------------------------------------------------------


def test_complete_pre_trust():
    init = DeferredInit()
    init.complete_pre_trust()
    assert init.is_pre_trust_complete
    assert not init.is_post_trust_complete
    assert not init.is_fully_initialized


# ------------------------------------------------------------------
# Elevate trust
# ------------------------------------------------------------------


def test_elevate_untrusted_to_sandboxed():
    init = DeferredInit(auto_elevate=False)
    init.elevate(TrustLevel.SANDBOXED)
    assert init.trust_level == TrustLevel.SANDBOXED


def test_elevate_sandboxed_to_trusted():
    init = DeferredInit(trust_level=TrustLevel.SANDBOXED, auto_elevate=False)
    init.elevate(TrustLevel.TRUSTED)
    assert init.trust_level == TrustLevel.TRUSTED


def test_elevate_to_same_level():
    init = DeferredInit(trust_level=TrustLevel.SANDBOXED, auto_elevate=False)
    init.elevate(TrustLevel.SANDBOXED)
    assert init.trust_level == TrustLevel.SANDBOXED


# ------------------------------------------------------------------
# Cannot lower trust
# ------------------------------------------------------------------


def test_cannot_lower_trust():
    init = DeferredInit(trust_level=TrustLevel.TRUSTED, auto_elevate=False)
    with pytest.raises(ValueError, match="Cannot lower trust level"):
        init.elevate(TrustLevel.UNTRUSTED)


def test_cannot_lower_trust_sandboxed_to_untrusted():
    init = DeferredInit(trust_level=TrustLevel.SANDBOXED, auto_elevate=False)
    with pytest.raises(ValueError, match="Cannot lower trust level"):
        init.elevate(TrustLevel.UNTRUSTED)


# ------------------------------------------------------------------
# requires_trust with auto_elevate=True
# ------------------------------------------------------------------


def test_requires_trust_auto_elevate():
    init = DeferredInit(auto_elevate=True)
    assert init.trust_level == TrustLevel.UNTRUSTED
    result = init.requires_trust(TrustLevel.TRUSTED)
    assert result is True
    assert init.trust_level == TrustLevel.TRUSTED


def test_requires_trust_auto_elevate_already_met():
    init = DeferredInit(trust_level=TrustLevel.TRUSTED, auto_elevate=True)
    result = init.requires_trust(TrustLevel.SANDBOXED)
    assert result is True
    assert init.trust_level == TrustLevel.TRUSTED


# ------------------------------------------------------------------
# requires_trust with auto_elevate=False
# ------------------------------------------------------------------


def test_requires_trust_no_auto_elevate_insufficient():
    init = DeferredInit(auto_elevate=False)
    result = init.requires_trust(TrustLevel.SANDBOXED)
    assert result is False
    assert init.trust_level == TrustLevel.UNTRUSTED


def test_requires_trust_no_auto_elevate_sufficient():
    init = DeferredInit(trust_level=TrustLevel.TRUSTED, auto_elevate=False)
    result = init.requires_trust(TrustLevel.SANDBOXED)
    assert result is True


# ------------------------------------------------------------------
# run_post_trust with auto_elevate=True
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_post_trust_auto_elevate():
    init = DeferredInit(auto_elevate=True)
    result = await init.run_post_trust()
    assert init.trust_level == TrustLevel.TRUSTED
    assert init.is_post_trust_complete
    assert result.tools_enabled is True
    assert isinstance(result, DeferredInitResult)


# ------------------------------------------------------------------
# run_post_trust without elevation raises PermissionError
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_post_trust_raises_without_elevation():
    init = DeferredInit(auto_elevate=False)
    with pytest.raises(PermissionError, match="Cannot run post-trust"):
        await init.run_post_trust()
    assert not init.is_post_trust_complete


@pytest.mark.asyncio
async def test_run_post_trust_works_after_manual_elevation():
    init = DeferredInit(auto_elevate=False)
    init.elevate(TrustLevel.SANDBOXED)
    result = await init.run_post_trust()
    assert init.is_post_trust_complete
    assert result.tools_enabled is True


# ------------------------------------------------------------------
# Post-trust callbacks
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_trust_sync_callbacks():
    init = DeferredInit(auto_elevate=True)
    init.register_post_trust_callback(lambda: "service-a")
    init.register_post_trust_callback(lambda: "service-b")
    result = await init.run_post_trust()
    assert result.external_services == ["service-a", "service-b"]


@pytest.mark.asyncio
async def test_post_trust_async_callbacks():
    async def async_callback():
        return "async-service"

    init = DeferredInit(auto_elevate=True)
    init.register_post_trust_callback(async_callback)
    result = await init.run_post_trust()
    assert result.external_services == ["async-service"]


@pytest.mark.asyncio
async def test_post_trust_callback_non_string_return():
    init = DeferredInit(auto_elevate=True)
    init.register_post_trust_callback(lambda: 42)
    result = await init.run_post_trust()
    assert result.external_services == []


@pytest.mark.asyncio
async def test_post_trust_callback_failure_does_not_abort():
    init = DeferredInit(auto_elevate=True)

    def failing():
        raise RuntimeError("boom")

    init.register_post_trust_callback(failing)
    init.register_post_trust_callback(lambda: "survivor")
    result = await init.run_post_trust()
    assert result.external_services == ["survivor"]
    assert init.is_post_trust_complete


# ------------------------------------------------------------------
# guard with auto_elevate
# ------------------------------------------------------------------


def test_guard_auto_elevate():
    init = DeferredInit(auto_elevate=True)
    init.guard("connect_mcp")
    assert init.trust_level == TrustLevel.SANDBOXED


def test_guard_auto_elevate_custom_minimum():
    init = DeferredInit(auto_elevate=True)
    init.guard("full_access", minimum=TrustLevel.TRUSTED)
    assert init.trust_level == TrustLevel.TRUSTED


def test_guard_already_sufficient():
    init = DeferredInit(trust_level=TrustLevel.TRUSTED, auto_elevate=False)
    init.guard("anything")  # should not raise


# ------------------------------------------------------------------
# guard without auto_elevate raises PermissionError
# ------------------------------------------------------------------


def test_guard_no_auto_elevate_raises():
    init = DeferredInit(auto_elevate=False)
    with pytest.raises(PermissionError, match="Operation 'connect_mcp'"):
        init.guard("connect_mcp")
    assert init.trust_level == TrustLevel.UNTRUSTED


def test_guard_no_auto_elevate_with_custom_minimum():
    init = DeferredInit(
        trust_level=TrustLevel.SANDBOXED, auto_elevate=False
    )
    with pytest.raises(PermissionError, match="TRUSTED"):
        init.guard("full_access", minimum=TrustLevel.TRUSTED)


# ------------------------------------------------------------------
# is_fully_initialized
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fully_initialized_requires_both_phases():
    init = DeferredInit(auto_elevate=True)
    assert not init.is_fully_initialized

    init.complete_pre_trust()
    assert not init.is_fully_initialized

    await init.run_post_trust()
    assert init.is_fully_initialized


@pytest.mark.asyncio
async def test_not_fully_initialized_without_pre_trust():
    init = DeferredInit(auto_elevate=True)
    await init.run_post_trust()
    assert not init.is_fully_initialized  # pre_trust not marked


# ------------------------------------------------------------------
# init_result populated after post-trust
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_result_populated():
    init = DeferredInit(auto_elevate=True)
    assert init.init_result is None
    result = await init.run_post_trust()
    assert init.init_result is result
    assert init.init_result.tools_enabled is True
    assert init.init_result.mcp_initialized is False
    assert init.init_result.plugins_loaded == []


@pytest.mark.asyncio
async def test_init_result_with_sandboxed_level():
    init = DeferredInit(
        trust_level=TrustLevel.SANDBOXED, auto_elevate=False
    )
    result = await init.run_post_trust()
    assert result.tools_enabled is True


@pytest.mark.asyncio
async def test_init_result_default_fields():
    result = DeferredInitResult()
    assert result.mcp_initialized is False
    assert result.plugins_loaded == []
    assert result.tools_enabled is False
    assert result.external_services == []
