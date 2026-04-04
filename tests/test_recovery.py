"""Tests for the workflow recovery worker."""

from __future__ import annotations

import asyncio
import time

import pytest

from sagewai.core.recovery import RecoveryWorker
from sagewai.core.state import InMemoryStore, StepStatus, WorkflowRun


class TestRecoveryWorker:
    @pytest.mark.asyncio
    async def test_worker_finds_and_reports_stale_runs(self):
        store = InMemoryStore()
        run = WorkflowRun(workflow_name="w", run_id="stale", status=StepStatus.RUNNING)
        await store.save_run(run)
        store._updated_at["w:stale"] = time.time() - 600

        recovered = []

        async def handler(wf_run: WorkflowRun) -> None:
            recovered.append(wf_run.run_id)

        worker = RecoveryWorker(store=store, handler=handler, interval=0.1, stale_timeout=300)
        task = asyncio.create_task(worker.start())

        await asyncio.sleep(0.3)
        worker.stop()
        await task

        assert "stale" in recovered

    @pytest.mark.asyncio
    async def test_worker_skips_waiting_runs(self):
        store = InMemoryStore()
        run = WorkflowRun(workflow_name="w", run_id="wait", status=StepStatus.WAITING)
        await store.save_run(run)
        store._updated_at["w:wait"] = time.time() - 600

        recovered = []

        async def handler(wf_run: WorkflowRun) -> None:
            recovered.append(wf_run.run_id)

        worker = RecoveryWorker(store=store, handler=handler, interval=0.1, stale_timeout=300)
        task = asyncio.create_task(worker.start())

        await asyncio.sleep(0.3)
        worker.stop()
        await task

        assert len(recovered) == 0
