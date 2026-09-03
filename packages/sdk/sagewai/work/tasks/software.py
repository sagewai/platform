# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The software ProfileRunner: git base, one labelled issue per step, GitHubIssueLifecycle."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.work.models import SUPERSEDED, WorkRecord
from sagewai.work.profiles.software.assembly import (
    ControllerFactory,
    SoftwareStack,
    build_github_lifecycle,
    build_software_stack,
)
from sagewai.work.profiles.software.github import (
    BaseMovedError,
    GitHubFactory,
    GitHubIssueLifecycle,
    GitHubPullRequest,
)
from sagewai.work.profiles.software.scm import fetch_default_branch_head
from sagewai.work.store import WorkStore
from sagewai.work.tasks.actions import DeliveryReceipt
from sagewai.work.tasks.budget import BudgetLedger, MeteredOperatorController
from sagewai.work.tasks.decisions import merge_policy_for
from sagewai.work.tasks.models import SoftwareTarget, Task
from sagewai.work.tasks.plan import PlanStep, TaskPlanResult
from sagewai.work.tasks.planner import TaskPlanner
from sagewai.work.tasks.scratch import ScratchWorkspaceManager

LABEL_PREFIX = "sagewai-task:"
STEP_MARKER = "sagewai-step:"
_STACK_CACHE_LIMIT = 8
_StackKey = tuple[str, str, bool, int, str, tuple[str, ...]]


def task_label(task: Task) -> str:
    return f"{LABEL_PREFIX}{task.id}"


def step_marker(task: Task, *, cycle: int, step: PlanStep) -> str:
    return f"{STEP_MARKER} {task.id}/{cycle}/{step.id}"


class SoftwareProfileRunner:
    """Every side effect the coordinator needs for a software Task."""

    def __init__(
        self,
        *,
        work_store: WorkStore,
        github_factory: GitHubFactory,
        engine: AsyncEngine | None = None,
        stack_cache_limit: int = _STACK_CACHE_LIMIT,
    ) -> None:
        self._work_store = work_store
        self._github_factory = github_factory
        self._engine = engine
        self._stacks: OrderedDict[_StackKey, SoftwareStack] = OrderedDict()
        self._stack_cache_limit = stack_cache_limit
        self._ledgers: dict[str, BudgetLedger] = {}

    def use_ledger(self, ledger: BudgetLedger) -> None:
        """Meter this Task's stage attempts into this cycle's ledger."""
        self._ledgers[ledger.task_id] = ledger

    async def aclose(self) -> None:
        """Flush every cached stack's activity sink; the runner owns the call."""
        for stack in tuple(self._stacks.values()):
            await stack.activity_sink.close()
        self._stacks.clear()
        self._ledgers.clear()

    async def base_sha(self, task: Task) -> str:
        """Fetch origin and return the default-branch head that the next Work pins."""
        target = self._target(task)
        repository = Path(target.repository_path)
        try:
            return await fetch_default_branch_head(repository, target.default_branch)
        except ValueError as exc:
            raise ValueError(f"{exc} for {target.owner}/{target.repo}") from exc

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
            worktree_manager=stack.worktree_manager,
            scratch_manager=ScratchWorkspaceManager(),
        )
        return await planner.plan(
            task,
            cycle=cycle,
            plan_version=plan_version,
            base_sha=base_sha,
            brief_text=brief_text,
            amendments=amendments,
        )

    async def find_issue(self, task: Task, *, cycle: int, step: PlanStep) -> str | None:
        """Read back a step issue created before a crash, by its body marker.

        The match is whole-line: ``.../1/s1`` is a prefix of ``.../1/s10``.
        """
        target = self._target(task)
        marker = step_marker(task, cycle=cycle, step=step)
        issues = await self._github_factory(task).list_labeled_issues(
            owner=target.owner, repo=target.repo, label=task_label(task)
        )
        return next(
            (
                issue.url
                for issue in issues
                if any(line.strip() == marker for line in issue.body.splitlines())
            ),
            None,
        )

    async def find_work(
        self, task: Task, *, issue_url: str, exclude: str | None = None
    ) -> WorkRecord | None:
        """The live Work bound to this issue, ignoring superseded reruns and ``exclude``.

        ``find_work_by_source_ref`` returns the oldest match, which after a base move is the
        superseded one, so the scan filters on status instead.
        """
        for record in await self._work_store.list_work(project_id=task.project_id):
            if record.source_ref != issue_url or record.status == SUPERSEDED:
                continue
            if exclude is not None and record.work_id == exclude:
                continue
            return record
        return None

    async def create_issue(self, task: Task, *, cycle: int, step: PlanStep) -> str:
        target = self._target(task)
        issue = await self._github_factory(task).create_issue(
            owner=target.owner,
            repo=target.repo,
            title=step.title,
            body=self._issue_body(task, cycle=cycle, step=step),
            labels=(task_label(task),),
        )
        return issue.url

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
        lifecycle = self._lifecycle(task, await self._stack(task))
        try:
            return await lifecycle.start(
                issue_url=issue_url,
                project_id=task.project_id,
                base_sha=base_sha,
                evidence_refs=evidence_refs,
            )
        except BaseMovedError:
            return await lifecycle.start(
                issue_url=issue_url,
                project_id=task.project_id,
                base_sha=await self.base_sha(task),
                evidence_refs=evidence_refs,
            )

    async def resume(self, task: Task, *, cycle: int, work_id: str) -> WorkRecord:
        stack = await self._stack(task)
        return await self._lifecycle(task, stack).resume(work_id, project_id=task.project_id)

    async def is_merged(self, task: Task, *, work_id: str) -> bool:
        """Confirm a merge-phase base-moved hold against GitHub before superseding."""
        record = await self._work_store.load_work(work_id, project_id=task.project_id)
        github = record.profile_context.get("github") or {}
        if not github:
            return False
        state = await self._github_factory(task).get_pull_request(
            GitHubPullRequest(
                project_id=task.project_id,
                owner=github["owner"],
                repo=github["repo"],
                number=int(github["pull_request_number"]),
                url=str(github["pull_request_url"]),
                head=str(github["branch"]),
                head_sha=str(github["branch_sha"]),
                base=str(github["default_branch"]),
            )
        )
        return state.merged

    async def deliver(
        self, task: Task, *, work_id: str, sink_version: int
    ) -> tuple[WorkRecord, tuple[DeliveryReceipt, ...]]:
        raise ValueError(f"task {task.id} is a software Task and has no deliver stage")

    @staticmethod
    def _target(task: Task) -> SoftwareTarget:
        if task.profile != "software" or not isinstance(task.target, SoftwareTarget):
            raise ValueError(f"task {task.id} is not a software Task")
        return task.target

    @staticmethod
    def _issue_body(task: Task, *, cycle: int, step: PlanStep) -> str:
        criteria = "\n".join(f"- {item.statement}" for item in step.acceptance_criteria)
        scope = ", ".join(step.allowed_scope)
        return (
            f"{step.goal}\n\n"
            f"Acceptance criteria:\n{criteria}\n\n"
            f"Allowed scope: {scope}\n\n"
            f"{step_marker(task, cycle=cycle, step=step)}\n"
        )

    async def _stack(self, task: Task) -> SoftwareStack:
        """One stack per Task and route; rebuilding it per step would re-probe the backends."""
        key = self._stack_key(task)
        cached = self._stacks.get(key)
        if cached is not None:
            self._stacks.move_to_end(key)
            return cached
        target = self._target(task)
        stack = await build_software_stack(
            project_id=task.project_id,
            repository=Path(target.repository_path),
            verification_image=target.verification_image,
            verification_commands=target.verification_commands,
            execution=task.execution.route,
            fleet_org=task.execution.fleet_org_id,
            prefer_free_implementation=task.routing.prefer_free_implementation,
            max_attempts_per_stage=task.budget.max_attempts_per_stage,
            controller_factory=self._controller_factory(task.id),
            engine=self._engine,
        )
        self._stacks[key] = stack
        if len(self._stacks) > self._stack_cache_limit:
            await self._evict_oldest()
        return stack

    async def _evict_oldest(self) -> None:
        """Close the least recently used stack; its Task's ledger stays while another stack of that Task remains."""
        evicted_key, evicted = self._stacks.popitem(last=False)
        await evicted.activity_sink.close()
        if not any(key[0] == evicted_key[0] for key in self._stacks):
            self._ledgers.pop(evicted_key[0], None)

    @staticmethod
    def _stack_key(task: Task) -> _StackKey:
        target = SoftwareProfileRunner._target(task)
        return (
            task.id,
            task.execution.route,
            task.routing.prefer_free_implementation,
            task.budget.max_attempts_per_stage,
            target.verification_image,
            target.verification_commands,
        )

    def _lifecycle(self, task: Task, stack: SoftwareStack) -> GitHubIssueLifecycle:
        return build_github_lifecycle(
            stack,
            project_id=task.project_id,
            repository=Path(self._target(task).repository_path),
            github=self._github_factory(task),
            execution=task.execution.route,
            fleet_org=task.execution.fleet_org_id,
            merge_policy=merge_policy_for(task.authority),
            task_id=task.id,
        )

    def _controller_factory(self, task_id: str) -> ControllerFactory:
        """The stack is cached per Task, so the controllers read the current ledger each run."""
        return lambda **kwargs: MeteredOperatorController(
            ledger=lambda: self._ledgers[task_id], **kwargs
        )


__all__ = ["SoftwareProfileRunner", "step_marker", "task_label"]
