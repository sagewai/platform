# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Multi-worker × multi-Sealed-profile isolation tests.

These tests cover a v1.0 gap the existing Sealed test suite did NOT
exercise: when multiple workers run concurrently, each scoped to its
own security profile, do their effective env / secret_keys stay
isolated? Or can a worker's resolved cascade leak into another's?

The launch coordination plan flagged Sealed isolation as a v1.0
acceptance criterion. The single-worker cascade is exercised in
``tests/integration/test_sealed_cascade.py`` (Postgres-backed).
This file uses the file-backed ``BuiltinAdminStoreBackend`` so it
runs in CI without external services, and focuses on the *cross-
worker* isolation invariant.

Three scenarios:

1. Two workers, two profiles — verify each worker's effective env
   contains only its own profile's keys (no cross-contamination).

2. Two workers, *concurrent* cascade resolution — race condition
   probe: 50 concurrent resolutions across two profiles must produce
   correct per-resolution results (not interleaved).

3. Workflow-level allowlist enforcement under multi-worker load —
   a profile restricted to ``workflow=foo`` raises PermissionError
   when resolved against ``workflow=bar``, even when both workflows
   run concurrently.
"""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet


# ── fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def crypto():
    """A fresh Fernet-keyed crypto for each test."""
    from sagewai.sealed.crypto import Crypto
    return Crypto(Fernet.generate_key())


@pytest.fixture
async def two_profiles_backend(tmp_path, crypto, monkeypatch):
    """A BuiltinAdminStoreBackend pre-seeded with two distinct profiles.

    profile-a:
      env:    {SHARED_KEY: "alpha-value", ONLY_A: "a-only"}
      secrets: {API_KEY: "alpha-secret"}

    profile-b:
      env:    {SHARED_KEY: "beta-value", ONLY_B: "b-only"}
      secrets: {API_KEY: "beta-secret"}

    Yields the backend so tests can do further saves if needed.
    """
    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.refs import _BACKENDS

    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=crypto,
    )
    monkeypatch.setitem(_BACKENDS, "builtin", backend)

    await backend.save_profile(ProfileWritePayload(
        id="profile-a", name="Profile A",
        env={"SHARED_KEY": "alpha-value", "ONLY_A": "a-only"},
        secrets={"API_KEY": "alpha-secret"},
    ))
    await backend.save_profile(ProfileWritePayload(
        id="profile-b", name="Profile B",
        env={"SHARED_KEY": "beta-value", "ONLY_B": "b-only"},
        secrets={"API_KEY": "beta-secret"},
    ))
    return backend


# ── 1. Two workers, two profiles — basic isolation ───────────────


@pytest.mark.asyncio
async def test_two_workers_two_profiles_isolated(two_profiles_backend):
    """Each worker's resolved cascade contains only its own profile's keys."""
    from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile

    # Simulate two concurrent workers each scoped to their own profile.
    # (No system or workflow level — just user-scoped profile per worker.)
    eff_a = await resolve_security_profile(
        levels=[CascadeLevel(name="user", profile_ref="profile-a", overrides=None)],
    )
    eff_b = await resolve_security_profile(
        levels=[CascadeLevel(name="user", profile_ref="profile-b", overrides=None)],
    )

    # Worker A sees alpha values
    assert eff_a.env["SHARED_KEY"] == "alpha-value"
    assert eff_a.env["ONLY_A"] == "a-only"
    assert "ONLY_B" not in eff_a.env, (
        f"profile A leaked profile B's ONLY_B key: {eff_a.env}"
    )

    # Worker B sees beta values
    assert eff_b.env["SHARED_KEY"] == "beta-value"
    assert eff_b.env["ONLY_B"] == "b-only"
    assert "ONLY_A" not in eff_b.env, (
        f"profile B leaked profile A's ONLY_A key: {eff_b.env}"
    )

    # Secret-key sets isolated
    assert eff_a.secret_keys == {"API_KEY"}
    assert eff_b.secret_keys == {"API_KEY"}
    # The KEYS overlap (both profiles have "API_KEY") — but the VALUES
    # of those keys differ, and EffectiveProfile only carries names not
    # values for secrets, by design. The values are fetched at injection
    # time from the backend, scoped to the resolved profile.

    # cascade_origins should track that each came from its own profile
    # (the resolver records origins per-key)
    assert eff_a.cascade_origins.get("ONLY_A") == "user"
    assert eff_b.cascade_origins.get("ONLY_B") == "user"


# ── 2. Concurrent cascade resolution — race condition probe ─────


@pytest.mark.asyncio
async def test_concurrent_cascade_resolution_no_state_corruption(two_profiles_backend):
    """50 concurrent resolutions across both profiles produce correct results.

    Probes for race conditions in the resolver / backend / refs registry.
    A bug here would surface as occasional cross-profile leaks.
    """
    from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile

    async def resolve_a():
        return await resolve_security_profile(
            levels=[CascadeLevel(name="user", profile_ref="profile-a", overrides=None)],
        )

    async def resolve_b():
        return await resolve_security_profile(
            levels=[CascadeLevel(name="user", profile_ref="profile-b", overrides=None)],
        )

    # 25 concurrent A + 25 concurrent B = 50 total
    tasks = [resolve_a() for _ in range(25)] + [resolve_b() for _ in range(25)]
    results = await asyncio.gather(*tasks)

    # First 25 must all be profile A; last 25 all profile B
    a_results = results[:25]
    b_results = results[25:]

    for i, eff in enumerate(a_results):
        assert eff.env["SHARED_KEY"] == "alpha-value", (
            f"a-result[{i}] saw {eff.env.get('SHARED_KEY')!r} not 'alpha-value' — race!"
        )
        assert "ONLY_A" in eff.env
        assert "ONLY_B" not in eff.env, (
            f"a-result[{i}] leaked ONLY_B — concurrent state corruption"
        )

    for i, eff in enumerate(b_results):
        assert eff.env["SHARED_KEY"] == "beta-value", (
            f"b-result[{i}] saw {eff.env.get('SHARED_KEY')!r} not 'beta-value' — race!"
        )
        assert "ONLY_B" in eff.env
        assert "ONLY_A" not in eff.env, (
            f"b-result[{i}] leaked ONLY_A — concurrent state corruption"
        )


# ── 3. Workflow allowlist enforcement under multi-worker load ───


@pytest.mark.asyncio
async def test_workflow_allowlist_enforced_per_worker(tmp_path, crypto, monkeypatch):
    """A profile restricted to one workflow rejects calls from other workflows."""
    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.refs import _BACKENDS
    from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile

    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json", crypto=crypto,
    )
    monkeypatch.setitem(_BACKENDS, "builtin", backend)

    # Profile restricted to "build-portfolio" workflow only
    await backend.save_profile(ProfileWritePayload(
        id="restricted-profile", name="Restricted",
        env={"DEPLOY_TARGET": "github"},
        secrets={"GITHUB_TOKEN": "secret"},
        allowed_workflows=["build-portfolio"],
    ))

    # Worker running "build-portfolio" — allowed
    eff = await resolve_security_profile(
        levels=[CascadeLevel(
            name="user", profile_ref="restricted-profile", overrides=None,
        )],
        audit_context={"workflow_name": "build-portfolio"},
    )
    assert eff.env["DEPLOY_TARGET"] == "github"

    # Another worker running "delete-everything" — must be rejected
    with pytest.raises(PermissionError, match="not allowed for workflow"):
        await resolve_security_profile(
            levels=[CascadeLevel(
                name="user", profile_ref="restricted-profile", overrides=None,
            )],
            audit_context={"workflow_name": "delete-everything"},
        )


# ── 4. System + user cascade with two users — system shared, users isolated ──


@pytest.mark.asyncio
async def test_system_shared_users_isolated_two_workers(tmp_path, crypto, monkeypatch):
    """One system profile, two user profiles. System keys shared; user keys per-user."""
    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.refs import _BACKENDS
    from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile

    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json", crypto=crypto,
    )
    monkeypatch.setitem(_BACKENDS, "builtin", backend)

    # One system-level profile (org-wide defaults)
    await backend.save_profile(ProfileWritePayload(
        id="system", name="System",
        env={"ORG_REGION": "eu-west-1", "ORG_TIER": "production"},
        secrets={"ORG_API_KEY": "system-key"},
    ))
    # Two user-level profiles
    await backend.save_profile(ProfileWritePayload(
        id="user-a", name="User A",
        env={"USER_NAME": "alice", "ORG_TIER": "staging"},  # overrides system ORG_TIER
        secrets={"USER_KEY": "alice-key"},
    ))
    await backend.save_profile(ProfileWritePayload(
        id="user-b", name="User B",
        env={"USER_NAME": "bob"},
        secrets={"USER_KEY": "bob-key"},
    ))

    # Worker A: system + user-a
    eff_a = await resolve_security_profile(levels=[
        CascadeLevel(name="system", profile_ref="system", overrides=None),
        CascadeLevel(name="user", profile_ref="user-a", overrides=None),
    ])
    # Worker B: system + user-b
    eff_b = await resolve_security_profile(levels=[
        CascadeLevel(name="system", profile_ref="system", overrides=None),
        CascadeLevel(name="user", profile_ref="user-b", overrides=None),
    ])

    # Both workers see system-level keys (shared by design)
    assert eff_a.env["ORG_REGION"] == "eu-west-1"
    assert eff_b.env["ORG_REGION"] == "eu-west-1"

    # User overrides system on shared keys (user-a overrode ORG_TIER)
    assert eff_a.env["ORG_TIER"] == "staging", "user-a's override didn't take effect"
    assert eff_b.env["ORG_TIER"] == "production", "user-b inherited system ORG_TIER"

    # User-specific keys isolated
    assert eff_a.env["USER_NAME"] == "alice"
    assert eff_b.env["USER_NAME"] == "bob"
    assert eff_a.env.get("USER_NAME") != eff_b.env.get("USER_NAME"), "user names leaked"

    # Both have ORG_API_KEY (from system) AND USER_KEY (from their user)
    assert eff_a.secret_keys == {"ORG_API_KEY", "USER_KEY"}
    assert eff_b.secret_keys == {"ORG_API_KEY", "USER_KEY"}
