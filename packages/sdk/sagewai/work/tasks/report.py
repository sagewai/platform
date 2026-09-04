# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The report ProfileRunner: a scratch workspace, no repository, no issue (spec section 12)."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.sandbox.backend import SandboxBackend
from sagewai.sandbox.secret_provider import SecretProvider
from sagewai.work.models import SUPERSEDED, WorkRecord
from sagewai.work.profiles.report.assembly import (
    ControllerFactory,
    ReportStack,
    build_report_stack,
)
from sagewai.work.profiles.report.models import ReportContractContext
from sagewai.work.profiles.software.github import GitHubFactory
from sagewai.work.profiles.software.scm import SoftwareWorktreeManager
from sagewai.work.store import WorkStore
from sagewai.work.tasks.actions import DeliveryReceipt
from sagewai.work.tasks.assessment import TaskAssessmentResult
from sagewai.work.tasks.assessor import TaskAssessor
from sagewai.work.tasks.budget import BudgetLedger, MeteredOperatorController
from sagewai.work.tasks.models import ReportTarget, Task, TaskDefaults
from sagewai.work.tasks.plan import AcceptedPlan, PlanStep, TaskPlanResult
from sagewai.work.tasks.planner import TaskPlanner
from sagewai.work.tasks.store import TaskStore

_STACK_CACHE_LIMIT = 8
_StackKey = tuple[str, int, tuple[str, ...]]


def step_ref(task: Task, *, cycle: int, step: PlanStep) -> str:
    """A report step's durable external key; the coordinator's 'issue url' for it."""
    return f"report://{task.id}/{cycle}/{step.id}"


class ReportProfileRunner:
    """Every side effect the coordinator needs for a report Task."""

    def __init__(
        self,
        *,
        work_store: WorkStore,
        github_factory: GitHubFactory | None = None,
        engine: AsyncEngine | None = None,
        sandbox: SandboxBackend | None = None,
        connection_store: Any = None,
        credentials: Any = None,
        secret_provider: SecretProvider | None = None,
        stack_cache_limit: int = _STACK_CACHE_LIMIT,
    ) -> None:
        self._work_store = work_store
        self._github_factory = github_factory
        self._engine = engine
        self._sandbox = sandbox
        self._connection_store = connection_store
        self._credentials = credentials
        self._secret_provider = secret_provider
        self._stacks: OrderedDict[_StackKey, ReportStack] = OrderedDict()
        self._stack_cache_limit = stack_cache_limit
        self._ledgers: dict[str, BudgetLedger] = {}

    def use_ledger(self, ledger: BudgetLedger) -> None:
        """Meter this Task's stage attempts into this cycle's ledger."""
        self._ledgers[ledger.task_id] = ledger

    async def aclose(self) -> None:
        """Flush every cached stack's activity sink; the wiring owns the call."""
        for stack in tuple(self._stacks.values()):
            await stack.activity_sink.close()
        self._stacks.clear()
        self._ledgers.clear()

    async def base_sha(self, task: Task) -> str | None:
        """A report has no repository; planning and assessment run in a scratch directory."""
        return None

    async def plan(
        self,
        task: Task,
        *,
        cycle: int,
        plan_version: int,
        base_sha: str | None,
        brief_text: str,
        amendments: tuple[str, ...],
    ) -> TaskPlanResult:
        stack = await self._stack(task)
        planner = TaskPlanner(
            work_store=stack.work_store,
            capsule_compiler=stack.capsule_compiler,
            controller=stack.read_controller,
            runtime=stack.analysis_runtime,
            capabilities=stack.read_capabilities,
            worktree_manager=SoftwareWorktreeManager(),
            scratch_manager=stack.scratch_manager,
        )
        return await planner.plan(
            task,
            cycle=cycle,
            plan_version=plan_version,
            base_sha=None,
            brief_text=brief_text,
            amendments=amendments,
        )

    async def find_issue(self, task: Task, *, cycle: int, step: PlanStep) -> str | None:
        return step_ref(task, cycle=cycle, step=step)

    async def create_issue(self, task: Task, *, cycle: int, step: PlanStep) -> str:
        return step_ref(task, cycle=cycle, step=step)

    async def find_work(
        self, task: Task, *, issue_url: str, exclude: str | None = None
    ) -> WorkRecord | None:
        """The Work an earlier crash already started for this step, by its source ref."""
        for record in await self._work_store.list_work(project_id=task.project_id):
            if (
                record.source_ref == issue_url
                and record.profile == "report"
                and record.status != SUPERSEDED
                and record.work_id != exclude
            ):
                return record
        return None

    async def start(
        self,
        task: Task,
        *,
        cycle: int,
        step: PlanStep,
        issue_url: str,
        base_sha: str | None,
        evidence_refs: tuple[str, ...] = (),
    ) -> WorkRecord:
        stack = await self._stack(task)
        return await stack.lifecycle.start(
            work_id=f"{task.id}:report:{cycle}:{step.id}",
            project_id=task.project_id,
            task=task,
            cycle=cycle,
            step=step,
            source_ref=issue_url,
            evidence_refs=evidence_refs,
        )

    async def resume(self, task: Task, *, cycle: int, work_id: str) -> WorkRecord:
        return await (await self._stack(task)).lifecycle.resume(work_id, project_id=task.project_id)

    async def is_merged(self, task: Task, *, work_id: str) -> bool:
        """A report is never merged; the base never moves under it."""
        return False

    async def deliver(
        self, task: Task, *, work_id: str, sink_version: int
    ) -> tuple[WorkRecord, tuple[DeliveryReceipt, ...]]:
        return await (await self._stack(task)).lifecycle.deliver(
            work_id, project_id=task.project_id, sink_version=sink_version
        )

    async def assess(
        self,
        task: Task,
        *,
        cycle: int,
        plan_version: int,
        plan: AcceptedPlan,
        outcomes: Mapping[str, str],
        merged_sha: str | None,
        evidence: tuple[str, ...],
    ) -> TaskAssessmentResult:
        stack = await self._stack(task)
        work_id = TaskAssessor.work_id(task, cycle=cycle, plan_version=plan_version)
        workspace = await stack.scratch_manager.prepare(
            project_id=task.project_id, work_id=work_id, attempt_id=f"assess-{plan_version}"
        )
        assessor = TaskAssessor(
            work_store=stack.work_store,
            capsule_compiler=stack.capsule_compiler,
            controller=stack.read_controller,
            runtime=stack.analysis_runtime,
            capabilities=stack.read_capabilities,
        )
        return await assessor.assess(
            task,
            cycle=cycle,
            plan_version=plan_version,
            plan=plan,
            outcomes=outcomes,
            workspace=workspace,
            evidence=evidence,
            profile_context=ReportContractContext(
                project_id=task.project_id,
                task_id=task.id,
                cycle=cycle,
                report_criterion_id=f"{work_id}:assessment",
            ).model_dump(mode="json"),
        )

    async def _stack(self, task: Task) -> ReportStack:
        target = self._target(task)
        key: _StackKey = (
            task.id,
            task.budget.max_attempts_per_stage,
            tuple(f"{sink.kind}:{sink.version}" for sink in target.sinks),
        )
        cached = self._stacks.get(key)
        if cached is not None:
            self._stacks.move_to_end(key)
            return cached
        defaults = await self._defaults(task)
        stack = await build_report_stack(
            project_id=task.project_id,
            target=target,
            harness_tiers=defaults.harness_tiers,
            github=(
                self._github_factory(task)
                if self._github_factory is not None
                and any(sink.kind == "github_issue" for sink in target.sinks)
                else None
            ),
            controller_factory=self._controller_factory(task.id),
            engine=self._engine,
            sandbox=self._sandbox,
            connection_store=self._connection_store,
            credentials=self._credentials,
            secret_provider=self._secret_provider,
        )
        self._stacks[key] = stack
        if len(self._stacks) > self._stack_cache_limit:
            await self._evict_oldest()
        return stack

    async def _evict_oldest(self) -> None:
        """Close the least recently used stack; drop its ledger after the Task's last stack."""
        evicted_key, evicted = self._stacks.popitem(last=False)
        await evicted.activity_sink.close()
        if not any(key[0] == evicted_key[0] for key in self._stacks):
            self._ledgers.pop(evicted_key[0], None)

    async def _defaults(self, task: Task) -> TaskDefaults:
        return await TaskStore(engine=self._engine).get_defaults(project_id=task.project_id)

    def _controller_factory(self, task_id: str) -> ControllerFactory:
        return lambda **kwargs: MeteredOperatorController(
            ledger=lambda: self._ledgers[task_id], **kwargs
        )

    @staticmethod
    def _target(task: Task) -> ReportTarget:
        if task.profile != "report" or not isinstance(task.target, ReportTarget):
            raise ValueError(f"task {task.id} is not a report Task")
        return task.target


__all__ = ["ReportProfileRunner", "step_ref"]
