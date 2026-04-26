# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for workflow.enqueue sandbox requirement threading."""
import pytest

from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxMode,
)


@pytest.fixture(autouse=True)
def _clear_sealed_registry():
    """Isolate the sealed backend registry for each test."""
    from sagewai.sealed import refs
    saved = refs._BACKENDS.copy()
    refs._BACKENDS.clear()
    yield
    refs._BACKENDS.clear()
    refs._BACKENDS.update(saved)


@pytest.fixture
def mock_store():
    class _MockStore:
        def __init__(self):
            self.last_saved_run = None

        async def save_run(self, run):
            self.last_saved_run = run

        async def load_run(self, *_):
            return None

        async def get_project_defaults(self, *_):
            return None

    return _MockStore()


@pytest.mark.asyncio
async def test_enqueue_explicit_requirements_persist(mock_store):
    """Caller passes explicit requirements; they land on the stored run."""
    from sagewai.core.state import DurableWorkflow

    wf = DurableWorkflow(name="wf", store=mock_store)
    run_id = await wf.enqueue(
        input_data={"x": 1},
        requires_sandbox_mode=SandboxMode.PER_RUN,
        requires_image="ghcr.io/sagewai/sandbox-ml:0.1.5",
        requires_network_policy=NetworkPolicy.FULL,
    )
    saved = mock_store.last_saved_run
    assert saved is not None
    assert saved.run_id == run_id
    assert saved.requires_sandbox_mode is SandboxMode.PER_RUN
    assert saved.requires_image == "ghcr.io/sagewai/sandbox-ml:0.1.5"
    assert saved.requires_network_policy is NetworkPolicy.FULL


@pytest.mark.asyncio
async def test_enqueue_falls_through_to_sdk_default(mock_store, caplog):
    """No explicit, no project defaults → SDK default with WARN logs.

    Note: ``sandbox_mode`` is now derived from ``execution_mode`` (BARE → NONE)
    at enqueue, so it never falls through. Only ``image`` and
    ``network_policy`` still emit fall-through warnings.
    """
    import logging

    from sagewai.core.state import DurableWorkflow

    wf = DurableWorkflow(name="wf", store=mock_store)
    with caplog.at_level(logging.WARNING, logger="sagewai.sandbox.resolution"):
        await wf.enqueue(input_data={"x": 1})
    saved = mock_store.last_saved_run
    assert saved.requires_sandbox_mode is SandboxMode.NONE
    warns = [r for r in caplog.records if r.levelname == "WARNING"]
    assert sum("sandbox resolution" in r.message for r in warns) == 2


@pytest.mark.asyncio
async def test_enqueue_resolves_sealed_cascade(tmp_path, monkeypatch):
    """workflow.enqueue resolves sealed levels and persists effective_*_keys on the run."""
    from cryptography.fernet import Fernet

    from sagewai.core.state import DurableWorkflow
    from sagewai.sealed import refs
    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.crypto import Crypto
    from sagewai.sealed.models import ProfileWritePayload

    # Set up a BuiltinAdminStoreBackend with a known profile
    crypto = Crypto(Fernet.generate_key())
    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=crypto,
        audit_writer=None,
    )
    refs._BACKENDS["builtin"] = backend

    await backend.save_profile(ProfileWritePayload(
        id="acme",
        name="Acme",
        secrets={"OPENAI_API_KEY": "sk-secret"},
        env={"DEBUG": "0"},
    ))

    # Point a minimal admin-state.json at a fresh file so AdminStateFile
    # won't fail — we just need it to return an empty sealed config so the
    # cascade falls through to the user-level profile_ref we pass at enqueue.
    import json
    state_file = tmp_path / "admin-state.json"
    state_file.write_text(json.dumps({"setup_complete": True, "sealed": {}}))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    wf = DurableWorkflow(name="my-workflow", store=mock_store_factory())
    run_id = await wf.enqueue(
        input_data={"x": 1},
        security_profile_ref="builtin://acme",
    )

    saved = wf._store.last_saved_run  # type: ignore[attr-defined]
    assert saved is not None
    assert saved.run_id == run_id
    # The last non-None profile_ref in the cascade was the user-level one
    assert saved.security_profile_ref == "builtin://acme"
    # Both env and secret keys are present
    assert set(saved.effective_env_keys) >= {"DEBUG", "OPENAI_API_KEY"}
    assert "OPENAI_API_KEY" in saved.effective_secret_keys
    # env key DEBUG is NOT a secret
    assert "DEBUG" not in saved.effective_secret_keys


def mock_store_factory():
    """Return a fresh mock store that also supports audit writes (has a mock _pool)."""
    from unittest.mock import AsyncMock, MagicMock

    class _MockStore:
        def __init__(self):
            self.last_saved_run = None
            # AuditWriter calls self._store._pool.execute(...) — provide a mock.
            # RevocationRegistry.find_active_for_keys calls _pool.fetch(...)
            # — return an empty list (no revocations) by default.
            pool = MagicMock()
            pool.execute = AsyncMock()
            pool.fetch = AsyncMock(return_value=[])
            self._pool = pool

        async def save_run(self, run):
            self.last_saved_run = run

        async def load_run(self, *_):
            return None

        async def get_project_defaults(self, *_):
            return None

    return _MockStore()


@pytest.mark.asyncio
async def test_enqueue_blocks_when_secret_is_revoked(tmp_path, monkeypatch):
    """Enqueue raises SecretRevokedError when a profile key is revoked."""
    import json
    from datetime import datetime, timezone

    from cryptography.fernet import Fernet

    from sagewai.core.state import DurableWorkflow
    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.crypto import Crypto
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.revocation import (
        Revocation,
        SecretRevokedError,
    )

    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=Crypto(Fernet.generate_key()),
        audit_writer=None,
    )
    monkeypatch.setattr("sagewai.sealed.refs._BACKENDS", {"builtin": backend})
    await backend.save_profile(ProfileWritePayload(
        id="acme", name="A", secrets={"K": "v"},
    ))

    # Stub registry that says K is revoked
    class _StubRegistry:
        async def find_active_for_keys(self, *, profile_id, secret_keys):
            return {
                "K": Revocation(
                    id=1,
                    profile_id=profile_id,
                    secret_key="K",
                    revoked_at=datetime.now(timezone.utc),
                    reason="leaked",
                    hard=False,
                )
            } if "K" in secret_keys else {}

    monkeypatch.setattr(
        "sagewai.core.state._build_revocation_registry",
        lambda store: _StubRegistry(),
    )

    # Point admin-state at a minimal file so AdminStateFile loads cleanly
    state_file = tmp_path / "admin-state.json"
    state_file.write_text(json.dumps({"setup_complete": True, "sealed": {}}))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    wf = DurableWorkflow(name="my-workflow", store=mock_store_factory())

    with pytest.raises(SecretRevokedError):
        await wf.enqueue(
            input_data={"x": 1},
            security_profile_ref="builtin://acme",
        )


# ── ExecutionMode (mode-aware runner) ────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_default_execution_mode_is_bare(mock_store):
    """Default enqueue produces a BARE run with derived sandbox_mode=NONE."""
    from sagewai.core.state import DurableWorkflow, ExecutionMode

    wf = DurableWorkflow(name="wf", store=mock_store)
    await wf.enqueue(input_data={"x": 1})
    saved = mock_store.last_saved_run
    assert saved.execution_mode is ExecutionMode.BARE
    assert saved.requires_sandbox_mode is SandboxMode.NONE


@pytest.mark.asyncio
async def test_enqueue_execution_mode_drives_sandbox_mode(mock_store):
    """SANDBOXED/IDENTITY/FULL/FULL_JIT all derive PER_RUN at enqueue."""
    from sagewai.core.state import DurableWorkflow, ExecutionMode

    for mode in (
        ExecutionMode.SANDBOXED,
        ExecutionMode.IDENTITY,
        ExecutionMode.FULL,
        ExecutionMode.FULL_JIT,
    ):
        wf = DurableWorkflow(name="wf", store=mock_store)
        await wf.enqueue(input_data={"x": 1}, execution_mode=mode)
        saved = mock_store.last_saved_run
        assert saved.execution_mode is mode
        assert saved.requires_sandbox_mode is SandboxMode.PER_RUN


@pytest.mark.asyncio
async def test_workflow_run_round_trips_execution_mode():
    """to_dict / from_dict preserve execution_mode."""
    from sagewai.core.state import ExecutionMode, WorkflowRun

    run = WorkflowRun(
        workflow_name="wf",
        run_id="r1",
        execution_mode=ExecutionMode.IDENTITY,
    )
    restored = WorkflowRun.from_dict(run.to_dict())
    assert restored.execution_mode is ExecutionMode.IDENTITY


def test_sandbox_mode_for_helper():
    """sandbox_mode_for: BARE→NONE, others→PER_RUN."""
    from sagewai.core.state import ExecutionMode, sandbox_mode_for

    assert sandbox_mode_for(ExecutionMode.BARE) is SandboxMode.NONE
    assert sandbox_mode_for(ExecutionMode.SANDBOXED) is SandboxMode.PER_RUN
    assert sandbox_mode_for(ExecutionMode.IDENTITY) is SandboxMode.PER_RUN
    assert sandbox_mode_for(ExecutionMode.FULL) is SandboxMode.PER_RUN
    assert sandbox_mode_for(ExecutionMode.FULL_JIT) is SandboxMode.PER_RUN
