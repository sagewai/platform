# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for LocalCacheSandboxPool cleanup hook + discard-on-failure."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.sealed.revocation import CleanupResult


@pytest.mark.asyncio
async def test_release_calls_provider_cleanup():
    """On release, pool calls provider.cleanup_run with run context."""
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    fake_provider = MagicMock()
    fake_provider.env_for = AsyncMock(return_value={})
    fake_provider.cleanup_run = AsyncMock(return_value=CleanupResult(
        env_keys_to_unset=["K"],
        audit_emitted=True,
        had_active_revocations=[],
    ))

    run = MagicMock()
    run.run_id = "r1"
    run.security_profile_ref = "acme"
    run.effective_env_keys = ["K"]
    run.effective_secret_keys = []
    run.project_id = "proj"

    handle = MagicMock()
    handle.stop = AsyncMock()

    await LocalCacheSandboxPool._release_with_cleanup(
        provider=fake_provider,
        run=run,
        handle=handle,
    )

    fake_provider.cleanup_run.assert_awaited_once()
    call = fake_provider.cleanup_run.await_args
    assert call.kwargs["run_id"] == "r1"
    assert call.kwargs["security_profile_ref"] == "acme"


@pytest.mark.asyncio
async def test_release_discards_sandbox_on_cleanup_exception():
    """When provider.cleanup_run raises, sandbox is stopped + not pooled."""
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    fake_provider = MagicMock()
    fake_provider.cleanup_run = AsyncMock(side_effect=RuntimeError("boom"))
    fake_provider._audit = MagicMock()
    fake_provider._audit.emit = AsyncMock()

    run = MagicMock()
    run.run_id = "r2"
    run.security_profile_ref = "acme"
    run.effective_env_keys = []
    run.effective_secret_keys = []
    run.project_id = "proj"

    handle = MagicMock()
    handle.stop = AsyncMock()

    result = await LocalCacheSandboxPool._release_with_cleanup(
        provider=fake_provider,
        run=run,
        handle=handle,
    )
    assert result == "discarded"
    handle.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_pools_sandbox_on_clean_path():
    """Happy path: sandbox is returned to pool (not discarded)."""
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    fake_provider = MagicMock()
    fake_provider.cleanup_run = AsyncMock(return_value=CleanupResult(
        env_keys_to_unset=[],
        audit_emitted=True,
        had_active_revocations=[],
    ))

    run = MagicMock()
    run.run_id = "r3"
    run.security_profile_ref = None
    run.effective_env_keys = []
    run.effective_secret_keys = []
    run.project_id = "proj"

    handle = MagicMock()
    handle.stop = AsyncMock()

    result = await LocalCacheSandboxPool._release_with_cleanup(
        provider=fake_provider,
        run=run,
        handle=handle,
    )
    assert result == "pooled"
    handle.stop.assert_not_awaited()
