"""Tests for PostgresStore — uses protocol compliance checks and integration tests."""

from __future__ import annotations

import os
import time

import pytest

from sagewai.core.state import StepRecord, StepStatus, WorkflowRun
from sagewai.core.stores.postgres import PostgresStore

DATABASE_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://sagecurator:sagecurator_password@localhost:5432/sagecurator",
    ),
)


@pytest.fixture
async def pg_store():
    """PostgresStore connected to the local dev PostgreSQL."""
    store = PostgresStore(database_url=DATABASE_URL)
    await store.initialize()
    # Clean workflow_runs before each test to avoid cross-test interference
    await store._pool.execute("DELETE FROM workflow_runs")
    yield store
    await store._pool.execute("DELETE FROM workflow_runs")
    await store.close()


class TestPostgresStoreProtocol:
    """Test PostgresStore satisfies WorkflowStore protocol."""

    def test_has_required_methods(self):
        """PostgresStore must have all WorkflowStore methods."""
        assert hasattr(PostgresStore, "save_run")
        assert hasattr(PostgresStore, "load_run")
        assert hasattr(PostgresStore, "list_runs")
        assert hasattr(PostgresStore, "recover_stale_runs")
        assert hasattr(PostgresStore, "heartbeat")

    def test_constructor_accepts_database_url(self):
        """PostgresStore can be created with a database_url string."""
        store = PostgresStore(database_url="postgresql://localhost/test")
        assert store is not None

    def test_constructor_accepts_pool(self):
        """PostgresStore can be created with an existing connection pool."""
        store = PostgresStore(pool=None)  # None is acceptable until first use
        assert store is not None


@pytest.mark.integration
class TestPostgresStoreIntegration:
    """Integration tests requiring a real PostgreSQL database.

    Run with: pytest -m integration --database-url=postgresql://...
    Skipped by default in CI unless PostgreSQL is available.
    """

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip(self, pg_store):
        run = WorkflowRun(workflow_name="test", run_id="r1", started_at=time.time())
        run.steps["s1"] = StepRecord(
            step_name="s1", status=StepStatus.COMPLETED, result="done", attempts=1
        )
        run.signals = {"sig": {"data": 1}}
        await pg_store.save_run(run)

        loaded = await pg_store.load_run("test", "r1")
        assert loaded is not None
        assert loaded.workflow_name == "test"
        assert loaded.run_id == "r1"
        assert loaded.steps["s1"].status == StepStatus.COMPLETED
        assert loaded.steps["s1"].result == "done"
        assert loaded.signals == {"sig": {"data": 1}}

    @pytest.mark.asyncio
    async def test_save_upsert_overwrites(self, pg_store):
        run = WorkflowRun(workflow_name="test", run_id="r2", status=StepStatus.RUNNING)
        await pg_store.save_run(run)

        run.status = StepStatus.COMPLETED
        run.output_data = "final"
        await pg_store.save_run(run)

        loaded = await pg_store.load_run("test", "r2")
        assert loaded.status == StepStatus.COMPLETED
        assert loaded.output_data == "final"

    @pytest.mark.asyncio
    async def test_list_runs_filters_by_status(self, pg_store):
        r1 = WorkflowRun(workflow_name="w", run_id="a", status=StepStatus.COMPLETED)
        r2 = WorkflowRun(workflow_name="w", run_id="b", status=StepStatus.RUNNING)
        r3 = WorkflowRun(workflow_name="w", run_id="c", status=StepStatus.FAILED)
        for r in [r1, r2, r3]:
            await pg_store.save_run(r)

        running = await pg_store.list_runs("w", status=StepStatus.RUNNING)
        assert len(running) == 1
        assert running[0].run_id == "b"

    @pytest.mark.asyncio
    async def test_recover_stale_runs(self, pg_store):
        run = WorkflowRun(workflow_name="w", run_id="stale", status=StepStatus.RUNNING)
        await pg_store.save_run(run)
        # Backdate updated_at directly in DB
        await pg_store._pool.execute(
            "UPDATE workflow_runs SET updated_at = NOW() - INTERVAL '10 minutes' WHERE id = $1",
            "w:stale",
        )

        stale = await pg_store.recover_stale_runs(stale_timeout_seconds=300)
        assert len(stale) == 1
        assert stale[0].run_id == "stale"

    @pytest.mark.asyncio
    async def test_heartbeat_refreshes_updated_at(self, pg_store):
        run = WorkflowRun(workflow_name="w", run_id="hb", status=StepStatus.RUNNING)
        await pg_store.save_run(run)
        await pg_store._pool.execute(
            "UPDATE workflow_runs SET updated_at = NOW() - INTERVAL '10 minutes' WHERE id = $1",
            "w:hb",
        )
        await pg_store.heartbeat("w", "hb")

        stale = await pg_store.recover_stale_runs(stale_timeout_seconds=300)
        assert len(stale) == 0

    @pytest.mark.asyncio
    async def test_load_nonexistent_returns_none(self, pg_store):
        result = await pg_store.load_run("nope", "nope")
        assert result is None

    @pytest.mark.asyncio
    async def test_workflow_run_persists_sandbox_requirements(self, pg_store):
        """Saving a WorkflowRun with sandbox requirements round-trips via Postgres."""
        from sagewai.sandbox.models import (
            NetworkPolicy,
            SandboxImageVariant,
            SandboxMode,
        )

        run = WorkflowRun(
            workflow_name="wf",
            run_id="r-sandbox-1",
            requires_sandbox_mode=SandboxMode.PER_RUN,
            requires_image="ghcr.io/sagewai/sandbox-ml:0.1.5",
            requires_variant=SandboxImageVariant.ML,
            requires_network_policy=NetworkPolicy.FULL,
        )
        await pg_store.save_run(run)
        loaded = await pg_store.load_run("wf", "r-sandbox-1")
        assert loaded is not None
        assert loaded.requires_sandbox_mode is SandboxMode.PER_RUN
        assert loaded.requires_image == "ghcr.io/sagewai/sandbox-ml:0.1.5"
        assert loaded.requires_variant is SandboxImageVariant.ML
        assert loaded.requires_network_policy is NetworkPolicy.FULL

    @pytest.mark.asyncio
    async def test_workflow_run_defaults_on_save(self, pg_store):
        """Saving a WorkflowRun without requirements uses safe defaults."""
        from sagewai.sandbox.models import NetworkPolicy, SandboxMode

        run = WorkflowRun(workflow_name="wf", run_id="r-sandbox-2")
        await pg_store.save_run(run)
        loaded = await pg_store.load_run("wf", "r-sandbox-2")
        assert loaded.requires_sandbox_mode is SandboxMode.NONE
        assert loaded.requires_image.startswith("ghcr.io/sagewai/sandbox-base:")
        assert loaded.requires_variant is None
        assert loaded.requires_network_policy is NetworkPolicy.NONE


@pytest.fixture
def admin_state_factory(tmp_path, monkeypatch):
    """Write a temporary admin-state.json with custom contents."""
    import json

    state_file = tmp_path / "admin-state.json"
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    def _write(**state_kwargs):
        state_file.write_text(json.dumps(state_kwargs))

    return _write


@pytest.mark.integration
class TestGetProjectDefaults:
    """Tests for PostgresStore.get_project_defaults() reading admin-state."""

    @pytest.mark.asyncio
    async def test_get_project_defaults_returns_configured(
        self, pg_store, admin_state_factory
    ):
        """Project with default_sandbox_requirements returns them as SandboxRequirements."""
        from sagewai.sandbox.models import (
            NetworkPolicy,
            SandboxMode,
        )

        admin_state_factory(
            projects=[
                {
                    "slug": "acme",
                    "default_sandbox_requirements": {
                        "sandbox_mode": "per_run",
                        "image": "ghcr.io/sagewai/sandbox-ml:0.1.5",
                        "network_policy": "full",
                    },
                }
            ]
        )
        defaults = await pg_store.get_project_defaults("acme")
        assert defaults is not None
        assert defaults.sandbox_mode is SandboxMode.PER_RUN
        assert defaults.image == "ghcr.io/sagewai/sandbox-ml:0.1.5"
        assert defaults.network_policy is NetworkPolicy.FULL

    @pytest.mark.asyncio
    async def test_get_project_defaults_returns_none_for_unconfigured(
        self, pg_store, admin_state_factory
    ):
        """Project not in admin-state OR project without defaults → None."""
        admin_state_factory(projects=[])
        assert await pg_store.get_project_defaults("ghost-project") is None

        admin_state_factory(projects=[{"slug": "acme", "name": "acme"}])  # no default_sandbox_requirements
        assert await pg_store.get_project_defaults("acme") is None
