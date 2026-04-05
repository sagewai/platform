# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Durable execution for workflow agents.

Bridges the gap between workflow agents (SequentialAgent, ParallelAgent,
LoopAgent) and the checkpoint/recovery system in DurableWorkflow. Provides
opt-in step-level checkpointing so workflow agents can resume from the
last successful step after a crash.

Usage::

    from sagewai.core.durability import DurabilityMode, DurableRunner
    from sagewai.core.state import InMemoryStore

    runner = DurableRunner(store=InMemoryStore())
    result = await runner.run_sequential(
        agents=[researcher, writer, reviewer],
        input_text="quantum computing",
        run_id="my-run-1",
    )

    # Resume after crash
    result = await runner.run_sequential(
        agents=[researcher, writer, reviewer],
        input_text="quantum computing",
        run_id="my-run-1",  # same run_id → resumes from checkpoint
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from sagewai.core.state import InMemoryStore, StepStatus, WorkflowRun
from sagewai.errors import SagewaiTimeoutError

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent
    from sagewai.core.compactor import PromptCompactor
    from sagewai.core.state import WorkflowStore

logger = logging.getLogger(__name__)


class StepTimeoutError(SagewaiTimeoutError):
    """Raised when a workflow step exceeds its timeout."""

    def __init__(self, step_name: str, timeout: float) -> None:
        super().__init__(f"Step '{step_name}' timed out after {timeout}s")
        self.step_name = step_name
        self.timeout = timeout


class DurabilityMode(str, Enum):
    """Durability level for workflow agents."""

    NONE = "none"
    CHECKPOINT = "checkpoint"


@dataclass
class CheckpointRecord:
    """A checkpoint for a single workflow step."""

    step_index: int
    step_name: str
    agent_name: str
    output: str
    compressed_context: list[dict[str, Any]] | None = None
    completed_at: float = 0.0


class DurableRunner:
    """Durable execution wrapper for workflow agents.

    Tracks each sub-agent execution as a named step. On resume (same run_id),
    completed steps are skipped and execution resumes from the first
    incomplete step.

    Parameters
    ----------
    store:
        Workflow state store. Defaults to InMemoryStore.
    compactor:
        Optional PromptCompactor for compressing context before checkpoint.
    heartbeat_interval:
        Seconds between heartbeat emissions during step execution.
        Prevents long-running steps from being detected as stale.
        Defaults to 30.0.
    step_timeout:
        Maximum seconds a single step may run before raising
        ``StepTimeoutError``. ``None`` (default) means no timeout.
    on_progress:
        Optional callback invoked after each step completes.
        Signature: ``(current_step, total_steps, step_name) -> None``.
    """

    def __init__(
        self,
        store: WorkflowStore | None = None,
        compactor: PromptCompactor | None = None,
        heartbeat_interval: float = 30.0,
        step_timeout: float | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self._store = store or InMemoryStore()
        self._compactor = compactor
        self._heartbeat_interval = heartbeat_interval
        self._step_timeout = step_timeout
        self._on_progress = on_progress

    async def run_sequential(
        self,
        agents: list[BaseAgent],
        input_text: str,
        run_id: str | None = None,
    ) -> str:
        """Execute agents sequentially with checkpointing.

        Each agent's execution is a checkpoint boundary. On resume, completed
        agents are skipped and their stored output is used.

        Args:
            agents: List of agents to execute in order.
            input_text: Initial input text.
            run_id: Run ID for checkpoint tracking. Auto-generated if None.

        Returns:
            Output from the last agent.
        """
        workflow_name = f"sequential:{'-'.join(a.config.name for a in agents)}"
        run_id = run_id or f"seq-{id(agents)}-{int(time.time())}"

        wf_run = await self._load_or_create(workflow_name, run_id, input_text)

        current_input = input_text
        for i, agent in enumerate(agents):
            step_name = f"step_{i}_{agent.config.name}"

            # Check if already completed (resume)
            existing = wf_run.steps.get(step_name)
            if existing and existing.status == StepStatus.COMPLETED:
                logger.info(
                    "Skipping completed step %s (resuming)",
                    step_name,
                )
                current_input = existing.result
                continue

            # Mark RUNNING and save BEFORE execution
            record = self._create_step_record(step_name)
            record.status = StepStatus.RUNNING
            wf_run.steps[step_name] = record
            await self._store.save_run(wf_run)

            try:
                result = await self._run_with_heartbeat(
                    agent.chat(current_input), wf_run, step_name,
                )
                self._complete_step(record, result, agent.config.name, i)
                current_input = result
            except Exception as exc:
                self._fail_step(record, wf_run, str(exc))
                await self._store.save_run(wf_run)
                raise

            await self._store.save_run(wf_run)

            if self._on_progress:
                self._on_progress(
                    i + 1, len(agents), agent.config.name,
                )

        self._complete_run(wf_run, current_input)
        await self._store.save_run(wf_run)
        return current_input

    async def run_parallel(
        self,
        agents: list[BaseAgent],
        input_text: str,
        merge: Callable[[list[str]], str] | None = None,
        run_id: str | None = None,
    ) -> str:
        """Execute agents in parallel with checkpointing.

        Already-completed agents are skipped on resume. Remaining agents
        run concurrently. All results are merged.

        Args:
            agents: List of agents to execute concurrently.
            input_text: Input text (sent to all agents).
            merge: Custom merge function. Default: join with newlines.
            run_id: Run ID for checkpoint tracking.

        Returns:
            Merged output from all agents.
        """
        merge_fn = merge or (lambda results: "\n\n".join(results))
        workflow_name = f"parallel:{'-'.join(a.config.name for a in agents)}"
        run_id = run_id or f"par-{id(agents)}-{int(time.time())}"

        wf_run = await self._load_or_create(workflow_name, run_id, input_text)

        results: dict[int, str] = {}
        pending: list[tuple[int, Any]] = []

        for i, agent in enumerate(agents):
            step_name = f"step_{i}_{agent.config.name}"
            existing = wf_run.steps.get(step_name)

            if existing and existing.status == StepStatus.COMPLETED:
                logger.info("Skipping completed step %s (resuming)", step_name)
                results[i] = existing.result
            else:
                pending.append((i, agent))

        # Mark pending agents RUNNING before concurrent execution
        if pending:
            for i, agent in pending:
                step_name = f"step_{i}_{agent.config.name}"
                record = self._create_step_record(step_name)
                record.status = StepStatus.RUNNING
                wf_run.steps[step_name] = record
            await self._store.save_run(wf_run)

            coros = [
                self._run_with_heartbeat(
                    agent.chat(input_text),
                    wf_run,
                    f"step_{i}_{agent.config.name}",
                )
                for i, agent in pending
            ]
            outputs = await asyncio.gather(*coros)

            for (i, agent), output in zip(pending, outputs):
                step_name = f"step_{i}_{agent.config.name}"
                record = wf_run.steps[step_name]
                self._complete_step(record, output, agent.config.name, i)
                results[i] = output

        # Merge in order
        ordered = [results[i] for i in range(len(agents))]
        merged = merge_fn(ordered)

        self._complete_run(wf_run, merged)
        await self._store.save_run(wf_run)
        return merged

    async def run_loop(
        self,
        agent: BaseAgent,
        input_text: str,
        max_iterations: int = 10,
        should_stop: Callable[[str, int], bool] | None = None,
        run_id: str | None = None,
    ) -> str:
        """Execute an agent in a loop with checkpointing per iteration.

        On resume, completed iterations are skipped.

        Args:
            agent: Agent to loop.
            input_text: Initial input text.
            max_iterations: Maximum loop iterations.
            should_stop: Optional stop condition callback.
            run_id: Run ID for checkpoint tracking.

        Returns:
            Output from the last iteration.
        """
        workflow_name = f"loop:{agent.config.name}"
        run_id = run_id or f"loop-{id(agent)}-{int(time.time())}"

        wf_run = await self._load_or_create(workflow_name, run_id, input_text)

        current_input = input_text
        last_result = ""

        for iteration in range(max_iterations):
            step_name = f"iter_{iteration}"

            existing = wf_run.steps.get(step_name)
            if existing and existing.status == StepStatus.COMPLETED:
                logger.info("Skipping completed iteration %d (resuming)", iteration)
                last_result = existing.result
                current_input = last_result
                continue

            # Mark RUNNING and save BEFORE execution
            record = self._create_step_record(step_name)
            record.status = StepStatus.RUNNING
            wf_run.steps[step_name] = record
            await self._store.save_run(wf_run)

            try:
                last_result = await self._run_with_heartbeat(
                    agent.chat(current_input), wf_run, step_name,
                )
                self._complete_step(
                    record, last_result, agent.config.name, iteration,
                )
            except Exception as exc:
                self._fail_step(record, wf_run, str(exc))
                await self._store.save_run(wf_run)
                raise

            await self._store.save_run(wf_run)

            if self._on_progress:
                self._on_progress(
                    iteration + 1, max_iterations, agent.config.name,
                )

            if should_stop and should_stop(last_result, iteration):
                logger.debug("Loop stopped at iteration %d", iteration)
                break

            current_input = last_result

        self._complete_run(wf_run, last_result)
        await self._store.save_run(wf_run)
        return last_result

    async def get_checkpoint(self, workflow_name: str, run_id: str) -> WorkflowRun | None:
        """Retrieve checkpoint state for a run."""
        return await self._store.load_run(workflow_name, run_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_with_heartbeat(
        self,
        coro: Awaitable[str],
        wf_run: WorkflowRun,
        step_name: str,
    ) -> str:
        """Run a coroutine with periodic heartbeat emission.

        Starts a background task that calls ``store.heartbeat()`` every
        ``heartbeat_interval`` seconds so long-running steps are not
        mistakenly detected as stale. If ``step_timeout`` is configured,
        the coroutine is wrapped with ``asyncio.wait_for``.

        Args:
            coro: The awaitable to execute (typically ``agent.chat(...)``).
            wf_run: Current workflow run (used for heartbeat identity).
            step_name: Name of the step (used for timeout error context).

        Returns:
            The string result of *coro*.

        Raises:
            StepTimeoutError: If *step_timeout* is set and exceeded.
        """

        async def _heartbeat_loop() -> None:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                try:
                    await self._store.heartbeat(
                        wf_run.workflow_name, wf_run.run_id,
                    )
                except Exception:
                    logger.warning(
                        "Heartbeat failed for %s:%s",
                        wf_run.workflow_name,
                        wf_run.run_id,
                    )

        heartbeat_task = asyncio.create_task(_heartbeat_loop())
        try:
            if self._step_timeout is not None:
                return await asyncio.wait_for(
                    coro, timeout=self._step_timeout,
                )
            return await coro
        except asyncio.TimeoutError:
            raise StepTimeoutError(step_name, self._step_timeout or 0.0)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _load_or_create(
        self, workflow_name: str, run_id: str, input_data: Any
    ) -> WorkflowRun:
        """Load existing run or create new one."""
        wf_run = await self._store.load_run(workflow_name, run_id)
        if wf_run is None:
            wf_run = WorkflowRun(
                workflow_name=workflow_name,
                run_id=run_id,
                input_data=input_data,
                started_at=time.time(),
            )
        wf_run.status = StepStatus.RUNNING
        await self._store.save_run(wf_run)
        return wf_run

    @staticmethod
    def _create_step_record(step_name: str) -> Any:
        from sagewai.core.state import StepRecord

        return StepRecord(step_name=step_name, started_at=time.time())

    @staticmethod
    def _complete_step(
        record: Any, result: str, agent_name: str, step_index: int
    ) -> None:
        record.status = StepStatus.COMPLETED
        record.result = result
        record.completed_at = time.time()

    @staticmethod
    def _fail_step(record: Any, wf_run: WorkflowRun, error: str) -> None:
        record.status = StepStatus.FAILED
        record.error = error
        record.completed_at = time.time()
        wf_run.status = StepStatus.FAILED

    @staticmethod
    def _complete_run(wf_run: WorkflowRun, output: Any) -> None:
        wf_run.status = StepStatus.COMPLETED
        wf_run.output_data = output
        wf_run.completed_at = time.time()
