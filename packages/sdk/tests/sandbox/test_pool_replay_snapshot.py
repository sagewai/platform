# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Sealed-iii.C — LocalCacheSandboxPool routes replay_snapshot to
SealedSecretProvider.replay_env_for instead of env_for.

Unit-tests the _build_env dispatch directly; full Docker-gated acquire
flow is covered by the e2e test in tests/integration/."""
from __future__ import annotations

import pytest

from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool
from sagewai.sandbox.models import SandboxConfig
from sagewai.sandbox.null_backend import NullBackend
from sagewai.sealed.replay import InjectionSnapshot, RotationDriftError


class _RecordingSecretProvider:
    def __init__(self, replay_result=None, replay_raises=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._replay_result = replay_result or {"A": "from-replay"}
        self._replay_raises = replay_raises

    async def env_for(self, **kwargs):
        self.calls.append(("env_for", kwargs))
        return {"A": "from-cascade"}

    async def replay_env_for(self, **kwargs):
        self.calls.append(("replay_env_for", kwargs))
        if self._replay_raises is not None:
            raise self._replay_raises
        return self._replay_result


def _make_pool(provider):
    return LocalCacheSandboxPool(
        backend=NullBackend(),
        config=SandboxConfig(),
        worker_id="t-worker",
        scratch_root=None,
        sealed_secret_provider=provider,
        audit_writer=None,
    )


@pytest.fixture
def snapshot():
    return InjectionSnapshot(
        effective_env_keys=["A"],
        effective_secret_keys=["A"],
        security_profile_ref="fake://p",
        secret_value_hashes={"A": "h"},
        secret_value_versions={"A": None},
        revocations_active_at_step={},
        captured_at=1.0,
    )


async def test_build_env_routes_to_replay_env_for_when_snapshot_present(
    snapshot,
):
    provider = _RecordingSecretProvider()
    pool = _make_pool(provider)

    env = await pool._build_env(
        project_id="proj",
        run_id="r2",
        security_profile_ref=None,
        effective_env_keys=None,
        effective_secret_keys=None,
        workflow_name=None,
        replay_snapshot=snapshot,
    )

    assert env == {"A": "from-replay"}
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == "replay_env_for"
    assert provider.calls[0][1]["snapshot"] is snapshot


async def test_build_env_uses_env_for_when_no_snapshot():
    provider = _RecordingSecretProvider()
    pool = _make_pool(provider)

    env = await pool._build_env(
        project_id="proj",
        run_id="r2",
        security_profile_ref="fake://p",
        effective_env_keys=["A"],
        effective_secret_keys=["A"],
        workflow_name=None,
        replay_snapshot=None,
    )

    assert env == {"A": "from-cascade"}
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == "env_for"


async def test_build_env_replay_path_propagates_rotation_drift(snapshot):
    """The replay path must NOT swallow exceptions — RotationDriftError
    has to bubble up so the worker fails the replay run with a clear cause."""
    provider = _RecordingSecretProvider(
        replay_raises=RotationDriftError("p", "A"),
    )
    pool = _make_pool(provider)

    with pytest.raises(RotationDriftError):
        await pool._build_env(
            project_id="proj",
            run_id="r2",
            security_profile_ref=None,
            effective_env_keys=None,
            effective_secret_keys=None,
            workflow_name=None,
            replay_snapshot=snapshot,
        )
