# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for SealedSecretProvider.cleanup_run."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.sealed.audit import AuditWriter
from sagewai.sealed.provider import SealedSecretProvider
from sagewai.sealed.revocation import CleanupResult, Revocation


def _fake_audit() -> AuditWriter:
    fake_store = MagicMock()
    fake_store._pool = MagicMock()
    fake_store._pool.execute = AsyncMock()
    return AuditWriter(fake_store)


@pytest.mark.asyncio
async def test_cleanup_run_returns_env_keys_to_unset():
    audit = _fake_audit()
    p = SealedSecretProvider(audit)
    result = await p.cleanup_run(
        run_id="r1",
        project_id="proj",
        sandbox_handle=MagicMock(),
        effective_env_keys=["A", "B"],
        effective_secret_keys=["A"],
        security_profile_ref="acme",
    )
    assert isinstance(result, CleanupResult)
    assert set(result.env_keys_to_unset) == {"A", "B"}
    assert result.audit_emitted is True


@pytest.mark.asyncio
async def test_cleanup_run_emits_pool_sandbox_reset_audit():
    audit = _fake_audit()
    p = SealedSecretProvider(audit)
    await p.cleanup_run(
        run_id="r1",
        project_id="proj",
        sandbox_handle=MagicMock(),
        effective_env_keys=["A"],
        effective_secret_keys=[],
        security_profile_ref="acme",
    )
    types = [c.args[1] for c in audit._store._pool.execute.await_args_list
             if c.args and len(c.args) > 1]
    assert "pool.sandbox.reset" in types


@pytest.mark.asyncio
async def test_cleanup_run_includes_active_revocations_field():
    """When revocation registry has matches, had_active_revocations is populated."""
    class _StubRegistry:
        async def find_active_for_keys(self, *, profile_id, secret_keys):
            return {
                "K1": Revocation(
                    id=1, profile_id=profile_id, secret_key="K1",
                    revoked_at=datetime.now(timezone.utc), reason="r", hard=False,
                )
            }

    audit = _fake_audit()
    p = SealedSecretProvider(audit, revocation_registry=_StubRegistry())
    result = await p.cleanup_run(
        run_id="r1",
        project_id="proj",
        sandbox_handle=MagicMock(),
        effective_env_keys=["K1", "K2"],
        effective_secret_keys=["K1"],
        security_profile_ref="acme",
    )
    assert "K1" in result.had_active_revocations


@pytest.mark.asyncio
async def test_cleanup_run_no_profile_ref_returns_empty():
    """Run that didn't use sealed at all has nothing to scrub."""
    audit = _fake_audit()
    p = SealedSecretProvider(audit)
    result = await p.cleanup_run(
        run_id="r1",
        project_id="proj",
        sandbox_handle=MagicMock(),
        effective_env_keys=[],
        effective_secret_keys=[],
        security_profile_ref=None,
    )
    assert result.env_keys_to_unset == []
    assert result.had_active_revocations == []
