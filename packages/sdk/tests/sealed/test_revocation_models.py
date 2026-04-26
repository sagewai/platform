# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for Sealed-iii.A revocation models."""
from datetime import datetime, timezone

from sagewai.sealed.revocation import (
    CleanupResult,
    Revocation,
    RevocationCheckUnavailableError,
    SecretRevokedError,
)


def test_revocation_minimal_fields():
    r = Revocation(
        id=1,
        profile_id="acme",
        secret_key="OPENAI_API_KEY",
        revoked_at=datetime.now(timezone.utc),
        revoked_by=None,
        reason="leaked",
        hard=False,
        lifted_at=None,
        lifted_by=None,
    )
    assert r.profile_id == "acme"
    assert r.hard is False
    assert r.lifted_at is None


def test_cleanup_result_defaults():
    cr = CleanupResult(
        env_keys_to_unset=["A", "B"],
        audit_emitted=True,
        had_active_revocations=[],
    )
    assert cr.error is None


def test_secret_revoked_error_carries_context():
    err = SecretRevokedError(
        profile_id="acme",
        secret_key="OPENAI_API_KEY",
        revocation_id=42,
        reason="leaked",
    )
    assert err.profile_id == "acme"
    assert err.revocation_id == 42
    assert "OPENAI_API_KEY" in str(err)


def test_revocation_check_unavailable_error_message():
    err = RevocationCheckUnavailableError("postgres unreachable")
    assert "postgres" in str(err)
