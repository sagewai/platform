# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Sealed-iii.C — SealedSecretProvider.replay_env_for.

Covers Tasks 9-11 of the plan: happy path, rotation handling
(history + fail-loud), and revocation-now-active warning.
"""
from __future__ import annotations

import pytest
from sagewai.sealed.models import Profile
from sagewai.sealed.provider import SealedSecretProvider
from sagewai.sealed.replay import (
    InjectionSnapshot,
    RotationDriftError,
    hash_secret_value,
)
from sagewai.sealed.revocation import Revocation


class _FakeBackend:
    def __init__(self, secrets, env=None, history=None) -> None:
        self.scheme = "fake"
        self.name = "fake"
        self._secrets = secrets
        self._env = env or {}
        self._history = history or {}  # {(key, version_id): value}

    async def get_profile(self, profile_id: str):
        return Profile(
            id=profile_id, name=profile_id,
            env=self._env, secrets=self._secrets,
        )

    async def supports_value_history(self) -> bool:
        return bool(self._history)

    async def get_secret_at_version(self, profile_id, secret_key, version_id):
        return self._history[(secret_key, version_id)]


class _FakeAudit:
    def __init__(self):
        self.events = []

    async def emit(self, *, event_type, **kwargs):
        self.events.append((event_type, kwargs))

    def types(self):
        return {e[0] for e in self.events}


class _FakeRegistry:
    def __init__(self, actives):
        self._actives = actives

    async def find_active_for_keys(self, *, profile_id, secret_keys):
        return {k: r for k, r in self._actives.items() if k in secret_keys}


def _patch_backend(monkeypatch, backend):
    monkeypatch.setattr(
        "sagewai.sealed.refs.resolve_backend",
        lambda ref: backend,
    )
    monkeypatch.setattr(
        "sagewai.sealed.refs.ProfileRef.parse",
        classmethod(lambda cls, s: type("R", (), {"path": "p"})()),
    )


# --- Task 9: happy path ----------------------------------------------------


async def test_replay_env_for_returns_current_value_when_hash_matches(
    monkeypatch,
):
    backend = _FakeBackend(
        secrets={"A": "alpha"}, env={"E": "envval"},
    )
    _patch_backend(monkeypatch, backend)
    audit = _FakeAudit()
    snap = InjectionSnapshot(
        effective_env_keys=["A", "E"],
        effective_secret_keys=["A"],
        security_profile_ref="fake://p",
        secret_value_hashes={"A": hash_secret_value("alpha")},
        secret_value_versions={"A": None},
        revocations_active_at_step={},
        captured_at=1.0,
    )
    provider = SealedSecretProvider(audit_writer=audit)
    env = await provider.replay_env_for(
        project_id="proj", run_id="r2", agent_id=None, snapshot=snap,
    )
    assert env == {"A": "alpha", "E": "envval"}
    assert "replay.snapshot_loaded" in audit.types()


async def test_replay_env_for_no_profile_ref_returns_empty(monkeypatch):
    audit = _FakeAudit()
    snap = InjectionSnapshot(
        effective_env_keys=[],
        effective_secret_keys=[],
        security_profile_ref=None,
        secret_value_hashes={},
        secret_value_versions={},
        revocations_active_at_step={},
        captured_at=1.0,
    )
    provider = SealedSecretProvider(audit_writer=audit)
    env = await provider.replay_env_for(
        project_id="p", run_id="r", agent_id=None, snapshot=snap,
    )
    assert env == {}


# --- Task 10: rotation handling --------------------------------------------


async def test_rotation_with_history_fetches_original_version(monkeypatch):
    backend = _FakeBackend(
        secrets={"A": "ROTATED"},
        history={("A", "v1"): "ORIGINAL"},
    )
    _patch_backend(monkeypatch, backend)
    audit = _FakeAudit()
    snap = InjectionSnapshot(
        effective_env_keys=["A"],
        effective_secret_keys=["A"],
        security_profile_ref="fake://p",
        secret_value_hashes={"A": hash_secret_value("ORIGINAL")},
        secret_value_versions={"A": "v1"},
        revocations_active_at_step={},
        captured_at=0.0,
    )
    provider = SealedSecretProvider(audit_writer=audit)
    env = await provider.replay_env_for(
        project_id="p", run_id="r", agent_id=None, snapshot=snap,
    )
    assert env["A"] == "ORIGINAL"
    assert "replay.rotation_detected" in audit.types()


async def test_rotation_no_history_raises_rotation_drift(monkeypatch):
    backend = _FakeBackend(secrets={"A": "ROTATED"})  # no history
    _patch_backend(monkeypatch, backend)
    audit = _FakeAudit()
    snap = InjectionSnapshot(
        effective_env_keys=["A"],
        effective_secret_keys=["A"],
        security_profile_ref="fake://p",
        secret_value_hashes={"A": hash_secret_value("ORIGINAL")},
        secret_value_versions={"A": None},
        revocations_active_at_step={},
        captured_at=0.0,
    )
    provider = SealedSecretProvider(audit_writer=audit)
    with pytest.raises(RotationDriftError):
        await provider.replay_env_for(
            project_id="p", run_id="r", agent_id=None, snapshot=snap,
        )
    assert "replay.failed_rotation_drift" in audit.types()


# --- Task 11: revocation snapshot warning ----------------------------------


async def test_replay_emits_used_revoked_snapshot_when_now_revoked(
    monkeypatch,
):
    backend = _FakeBackend(secrets={"A": "alpha"})
    _patch_backend(monkeypatch, backend)
    revocation = Revocation(
        id=42, profile_id="p", secret_key="A",
        revoked_at="2026-04-26T00:00:00",
        revoked_by="admin", reason="leaked", hard=False,
        lifted_at=None, lifted_by=None,
    )
    registry = _FakeRegistry({"A": revocation})
    audit = _FakeAudit()
    snap = InjectionSnapshot(
        effective_env_keys=["A"],
        effective_secret_keys=["A"],
        security_profile_ref="fake://p",
        secret_value_hashes={"A": hash_secret_value("alpha")},
        secret_value_versions={"A": None},
        revocations_active_at_step={},  # was NOT revoked at original time
        captured_at=0.0,
    )
    provider = SealedSecretProvider(
        audit_writer=audit, revocation_registry=registry,
    )
    env = await provider.replay_env_for(
        project_id="proj", run_id="r2", agent_id=None, snapshot=snap,
    )
    # Replay still uses the snapshot value
    assert env["A"] == "alpha"
    assert "replay.used_revoked_snapshot" in audit.types()


async def test_replay_no_warning_when_revocation_id_unchanged(
    monkeypatch,
):
    backend = _FakeBackend(secrets={"A": "alpha"})
    _patch_backend(monkeypatch, backend)
    revocation = Revocation(
        id=7, profile_id="p", secret_key="A",
        revoked_at="2026-04-26T00:00:00",
        revoked_by="admin", reason="planned", hard=False,
        lifted_at=None, lifted_by=None,
    )
    registry = _FakeRegistry({"A": revocation})
    audit = _FakeAudit()
    snap = InjectionSnapshot(
        effective_env_keys=["A"],
        effective_secret_keys=["A"],
        security_profile_ref="fake://p",
        secret_value_hashes={"A": hash_secret_value("alpha")},
        secret_value_versions={"A": None},
        revocations_active_at_step={"A": 7},  # SAME revocation id
        captured_at=0.0,
    )
    provider = SealedSecretProvider(
        audit_writer=audit, revocation_registry=registry,
    )
    await provider.replay_env_for(
        project_id="p", run_id="r", agent_id=None, snapshot=snap,
    )
    assert "replay.used_revoked_snapshot" not in audit.types()
