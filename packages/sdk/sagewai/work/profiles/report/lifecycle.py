# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Report Work lifecycle: compose, verify, review, then deliver by Task gate."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sagewai.artifacts.object_store import ArtifactStore
from sagewai.work.capsule import TaskCapsuleCompiler
from sagewai.work.contract import AcceptanceCriterion, WorkContract
from sagewai.work.control import OperatorController
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import (
    ActionRequest,
    ActionResult,
    ActionScope,
    ReviewResult,
    WorkItem,
    WorkRecord,
)
from sagewai.work.profiles.report.models import (
    ReportArchive,
    ReportClaim,
    ReportContractContext,
    ReportResult,
    SourceSnapshot,
)
from sagewai.work.profiles.report.profile import ReportProfile
from sagewai.work.profiles.report.verification import redact_text, verify_report
from sagewai.work.runtime import CapabilitySet, OperatorRuntime, WorkRequest
from sagewai.work.store import WorkStore
from sagewai.work.tasks.actions import DeliveryReceipt, deliver_action_id
from sagewai.work.tasks.models import ReportTarget, Task
from sagewai.work.tasks.plan import PlanStep
from sagewai.work.tasks.scratch import ScratchWorkspace, ScratchWorkspaceManager

_MAX_REPAIRS = 2
_STATUS = {
    "composing": "COMPOSING",
    "reviewing": "REVIEWING",
    "ready": "READY_TO_DELIVER",
    "complete": "COMPLETE",
    "blocked": "WORK_BLOCKED",
}


@dataclass(frozen=True)
class ReportOperator:
    """Runtime, controller, and capability boundary for one report role."""

    actor_ref: str
    runtime: OperatorRuntime
    controller: OperatorController
    capabilities: CapabilitySet


def _at_position(ladder: tuple[ReportOperator, ...], position: int) -> ReportOperator:
    """Runtime attempt N uses position N; the last position repeats (spec section 9.2)."""
    return ladder[min(position, len(ladder)) - 1]


def allowed_hosts(target: ReportTarget) -> tuple[str, ...]:
    """The hosts the browser grants scope the composer to."""
    hosts: list[str] = []
    for grant in target.sources:
        if grant.kind != "browser":
            continue
        hosts.extend(str(host) for host in grant.scope.get("allowed_hosts") or ())
    return tuple(dict.fromkeys(hosts))


def _findings_text(review: ReviewResult) -> str:
    """Every finding the reviewer raised, as one line the composer can act on."""
    findings = (
        *review.unsupported_claims,
        *review.introduced_assumptions,
        *review.scope_expansions,
        *review.unsupported_implementation_choices,
    )
    return "; ".join(findings) or "the reviewer rejected the report without naming a finding"


class ReportLifecycle:
    """One stage per resume: compose and verify, review, then deliver behind the Task's gate."""

    def __init__(
        self,
        *,
        profile: ReportProfile,
        work_store: WorkStore,
        capsule_compiler: TaskCapsuleCompiler,
        scratch_manager: ScratchWorkspaceManager,
        artifact_store: ArtifactStore,
        composer: tuple[ReportOperator, ...],
        reviewer: tuple[ReportOperator, ...],
        sinks: Mapping[str, Any],
        credential_values: Mapping[str, str] | None = None,
        max_repairs: int = _MAX_REPAIRS,
    ) -> None:
        self._profile = profile
        self._work_store = work_store
        self._capsule_compiler = capsule_compiler
        self._scratch = scratch_manager
        self._artifacts = artifact_store
        self._composer = composer
        self._reviewer = reviewer
        self._sinks = dict(sinks)
        self._credential_values = dict(credential_values or {})
        self._max_repairs = max_repairs

    async def start(
        self,
        *,
        work_id: str,
        project_id: str,
        task: Task,
        cycle: int,
        step: PlanStep,
        source_ref: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> WorkRecord:
        """Create the Work at COMPOSING; the coordinator's ResumeStep drives it from there."""
        target = task.target
        now = datetime.now(timezone.utc)
        work_item = WorkItem(
            id=work_id,
            project_id=project_id,
            profile=self._profile.name,
            source="task",
            source_ref=source_ref,
            title=step.title,
            description=step.goal,
            target_systems=("report",),
            created_at=now,
        )
        criterion_id = f"{work_id}:report"
        context = ReportContractContext(
            project_id=project_id,
            task_id=task.id,
            cycle=cycle,
            report_criterion_id=criterion_id,
            required_sections=target.required_sections,
            max_bytes=target.max_bytes,
            allowed_hosts=allowed_hosts(target),
            sinks=target.sinks,
        )
        contract = WorkContract(
            id=f"{work_id}:contract",
            project_id=project_id,
            work_id=work_id,
            version=1,
            goal=step.goal,
            allowed_scope=(".",),
            acceptance_criteria=(
                AcceptanceCriterion(
                    id=criterion_id,
                    project_id=project_id,
                    statement="the report is composed, verified, reviewed, and delivered",
                    verification_kind="profile",
                ),
            ),
            constraints=(),
            non_goals=(),
            evidence_refs=evidence_refs,
            assumption_ids=(),
            risk=step.risk,
            design_required=False,
            profile_context=context.model_dump(mode="json"),
        )
        await self._ensure_created(work_item, contract)
        record = await self._work_store.load_work(work_id, project_id=project_id)
        return await self._set(record, _STATUS["composing"])

    async def resume(self, work_id: str, *, project_id: str) -> WorkRecord:
        """Run the one stage the Work's status calls for."""
        record = await self._work_store.load_work(work_id, project_id=project_id)
        events = await self._work_store.read_events(work_id, project_id=project_id)
        work_item, contract = self._canonical(events)
        context = ReportContractContext.model_validate(contract.profile_context)
        if record.status == _STATUS["composing"]:
            return await self._compose(record, work_item, contract, context, events)
        if record.status == _STATUS["reviewing"]:
            return await self._review(record, work_item, contract, context, events)
        return record

    async def _compose(
        self,
        record: WorkRecord,
        work_item: WorkItem,
        contract: WorkContract,
        context: ReportContractContext,
        events: Sequence[WorkEvent],
    ) -> WorkRecord:
        attempt = self._attempts(events, "compose") + 1
        run_id = f"{work_item.id}:compose:{attempt}"
        operator = _at_position(self._composer, attempt)
        workspace = await self._scratch.prepare(
            project_id=work_item.project_id,
            work_id=work_item.id,
            attempt_id=f"compose-{attempt}",
        )
        await self._append(
            work_item,
            WorkEventType.STAGE_STARTED,
            {"stage": "compose", "run_id": run_id},
            actor_ref=operator.actor_ref,
        )
        capsule = await self._capsule_compiler.compile(
            work_item=work_item,
            contract=contract,
            stage="compose",
            search_text=contract.goal,
            profile_context={
                "report_result_schema": ReportResult.model_json_schema(),
                "attempt_id": run_id,
                "required_sections": list(context.required_sections),
                "allowed_hosts": list(context.allowed_hosts),
                "max_bytes": context.max_bytes,
                "findings": self._findings(events),
            },
        )
        result = await operator.controller.run(
            runtime=operator.runtime,
            request=WorkRequest(
                project_id=work_item.project_id,
                work_id=work_item.id,
                run_id=run_id,
                stage="compose",
                action_scope=ActionScope(
                    project_id=work_item.project_id,
                    objective=(
                        "Read every declared source, write report.md, and cite each claim "
                        "against the source file it came from"
                    ),
                    allowed_targets=(".",),
                    allowed_capabilities=tuple(g.name for g in operator.capabilities.grants),
                ),
                action_intents=(),
                control_preconditions=(),
            ),
            capsule=capsule,
            capabilities=operator.capabilities,
            workspace=workspace,
        )
        if result.status != "passed":
            return await self._maybe_repair(
                record,
                work_item,
                attempt,
                f"compose stage {result.status}: {result.summary[:500]}",
                actor_ref=operator.actor_ref,
            )
        composed = ReportResult.model_validate(result.profile_context["report_result"])
        archive, body = await self._persist(work_item, context, workspace, composed)
        failures = verify_report(
            body,
            archive,
            required_sections=context.required_sections,
            max_bytes=context.max_bytes,
            allowed_hosts=context.allowed_hosts,
        )
        await self._append(
            work_item,
            WorkEventType.VERIFICATION_RECORDED,
            {
                "stage": "verify",
                "run_id": run_id,
                "passed": not failures,
                "failures": list(failures),
                "report_ref": archive.report_ref,
            },
            actor_ref=operator.actor_ref,
        )
        if failures:
            return await self._maybe_repair(
                record, work_item, attempt, "; ".join(failures), actor_ref=operator.actor_ref
            )
        await self._append(
            work_item,
            WorkEventType.STAGE_COMPLETED,
            {"stage": "compose", "run_id": run_id, "evidence_refs": [archive.report_ref]},
            actor_ref=operator.actor_ref,
        )
        return await self._set(
            record,
            _STATUS["reviewing"],
            report={"archive": archive.model_dump(mode="json")},
        )

    async def _persist(
        self,
        work_item: WorkItem,
        context: ReportContractContext,
        workspace: ScratchWorkspace,
        composed: ReportResult,
    ) -> tuple[ReportArchive, str]:
        """Redact, hash, and store the report and every source the composer wrote."""
        body = redact_text(self._read(workspace, composed.report_path), self._credential_values)
        report = self._artifacts.put_bytes(
            body.encode("utf-8"),
            project_id=work_item.project_id,
            media_type="text/markdown",
            created_by=f"work:{work_item.id}",
        )
        snapshots: list[SourceSnapshot] = []
        by_url: dict[str, str] = {}
        for source in composed.sources_used:
            raw = redact_text(self._read(workspace, source.path), self._credential_values)
            encoded = raw.encode("utf-8")
            stored = self._artifacts.put_bytes(
                encoded,
                project_id=work_item.project_id,
                media_type="text/plain",
                created_by=f"work:{work_item.id}",
            )
            snapshots.append(
                SourceSnapshot(
                    snapshot_ref=stored.storage_ref,
                    url=source.url,
                    fetched_at=source.fetched_at,
                    content_sha256=hashlib.sha256(encoded).hexdigest(),
                    size_bytes=len(encoded),
                )
            )
            by_url[source.url] = stored.storage_ref
        archive = ReportArchive(
            report_ref=report.storage_ref,
            report_bytes=report.size_bytes,
            report_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            snapshots=tuple(snapshots),
            claims=tuple(
                ReportClaim(
                    statement=claim.statement,
                    snapshot_refs=tuple(by_url[url] for url in claim.source_urls if url in by_url),
                )
                for claim in composed.claims
            ),
        )
        return archive, body

    def _read(self, workspace: ScratchWorkspace, relative: str) -> str:
        path = (workspace.path / relative).resolve()
        if workspace.path.resolve() not in path.parents:
            raise ValueError(f"report path escapes the workspace: {relative}")
        return path.read_text(encoding="utf-8")

    async def _review(
        self,
        record: WorkRecord,
        work_item: WorkItem,
        contract: WorkContract,
        context: ReportContractContext,
        events: Sequence[WorkEvent],
    ) -> WorkRecord:
        attempt = self._attempts(events, "review") + 1
        run_id = f"{work_item.id}:review:{attempt}"
        operator = _at_position(self._reviewer, attempt)
        archive = ReportArchive.model_validate(record.profile_context["report"]["archive"])
        workspace = await self._scratch.prepare(
            project_id=work_item.project_id,
            work_id=work_item.id,
            attempt_id=f"review-{attempt}",
        )
        body = self._artifacts.read(archive.report_ref, project_id=work_item.project_id).decode(
            "utf-8"
        )
        await self._append(
            work_item,
            WorkEventType.STAGE_STARTED,
            {"stage": "review", "run_id": run_id},
            actor_ref=operator.actor_ref,
        )
        capsule = await self._capsule_compiler.compile(
            work_item=work_item,
            contract=contract,
            stage="review",
            search_text=contract.goal,
            profile_context={
                "review_result_schema": ReviewResult.model_json_schema(),
                "attempt_id": run_id,
                "report": body,
                "archive": archive.model_dump(mode="json"),
            },
        )
        result = await operator.controller.run(
            runtime=operator.runtime,
            request=WorkRequest(
                project_id=work_item.project_id,
                work_id=work_item.id,
                run_id=run_id,
                stage="review",
                action_scope=ActionScope(
                    project_id=work_item.project_id,
                    objective=(
                        "Ground every claim against its cited snapshot and report any "
                        "unsupported claim, assumption, or scope expansion"
                    ),
                    allowed_targets=(".",),
                    allowed_capabilities=tuple(g.name for g in operator.capabilities.grants),
                ),
                action_intents=(),
                control_preconditions=(),
            ),
            capsule=capsule,
            capabilities=operator.capabilities,
            workspace=workspace,
        )
        if result.status != "passed":
            return await self._maybe_repair(
                record,
                work_item,
                attempt,
                f"review stage {result.status}: {result.summary[:500]}",
                actor_ref=operator.actor_ref,
            )
        review = ReviewResult.model_validate(result.profile_context["review_result"])
        await self._append(
            work_item,
            WorkEventType.STAGE_COMPLETED,
            {"stage": "review", "run_id": run_id, "evidence_refs": list(result.evidence_refs)},
            actor_ref=operator.actor_ref,
        )
        await self._append(
            work_item,
            WorkEventType.REVIEW_RECORDED,
            review.model_dump(mode="json"),
            actor_ref=operator.actor_ref,
        )
        if review.verdict == "accept":
            return await self._ready(record, work_item, context, archive)
        if review.verdict == "repair":
            return await self._maybe_repair(
                record,
                work_item,
                attempt,
                _findings_text(review),
                actor_ref=operator.actor_ref,
            )
        return await self._block(
            record,
            work_item,
            "review_blocked",
            _findings_text(review),
            actor_ref=operator.actor_ref,
        )

    async def _maybe_repair(
        self,
        record: WorkRecord,
        work_item: WorkItem,
        attempt: int,
        detail: str,
        *,
        actor_ref: str,
    ) -> WorkRecord:
        """Section 12 step 3: two repairs, then the Work blocks for a human."""
        detail = redact_text(detail, self._credential_values)
        if attempt > self._max_repairs:
            return await self._block(
                record,
                work_item,
                "report_repairs_exhausted",
                detail,
                actor_ref=actor_ref,
            )
        await self._append(
            work_item,
            WorkEventType.OBSERVATION_RECORDED,
            {
                "check": "report_repair",
                "passed": False,
                "detail": detail,
                "evidence_refs": [],
            },
            actor_ref=actor_ref,
        )
        return await self._set(record, _STATUS["composing"])

    async def _block(
        self,
        record: WorkRecord,
        work_item: WorkItem,
        reason: str,
        detail: str,
        *,
        actor_ref: str = "profile:report",
    ) -> WorkRecord:
        detail = redact_text(detail, self._credential_values)
        await self._append(
            work_item,
            WorkEventType.WORK_BLOCKED,
            {"reason": reason, "decision_request": detail, "evidence_refs": []},
            actor_ref=actor_ref,
        )
        return await self._set(record, _STATUS["blocked"])

    async def _ready(
        self,
        record: WorkRecord,
        work_item: WorkItem,
        context: ReportContractContext,
        archive: ReportArchive,
    ) -> WorkRecord:
        """Record the deliver ActionRequest and stop; the deliver gate is the Task's."""
        pending = next(sink for sink in context.sinks if not _is_delivered(record, sink.version))
        action = self._sinks[pending.kind].action(
            project_id=work_item.project_id,
            work_id=work_item.id,
            sink=pending,
            archive=archive,
        )
        current = dict(record.profile_context["report"])
        return await self._set(
            record,
            _STATUS["ready"],
            report={
                **current,
                "archive": archive.model_dump(mode="json"),
                "pending_sink_version": pending.version,
                "deliver_action": action.model_dump(mode="json"),
            },
        )

    async def _ensure_created(self, work_item: WorkItem, contract: WorkContract) -> None:
        events = await self._work_store.read_events(work_item.id, project_id=work_item.project_id)
        if not any(event.event_type is WorkEventType.WORK_CREATED for event in events):
            sequence = events[-1].sequence + 1 if events else 1
            await self._work_store.append_events(
                (
                    self._event(
                        work_item,
                        sequence,
                        WorkEventType.WORK_CREATED,
                        work_item.model_dump(mode="json"),
                    ),
                    self._event(
                        work_item,
                        sequence + 1,
                        WorkEventType.CONTRACT_PROPOSED,
                        contract.model_dump(mode="json"),
                    ),
                )
            )
        if (
            await self._work_store.load_work(work_item.id, project_id=work_item.project_id)
            is not None
        ):
            return
        now = datetime.now(timezone.utc)
        await self._work_store.save_work(
            WorkRecord(
                work_id=work_item.id,
                project_id=work_item.project_id,
                source_ref=work_item.source_ref,
                profile=work_item.profile,
                status="PLANNING",
                contract_version=1,
                active_run_id=None,
                pending_gate=None,
                profile_context=dict(contract.profile_context),
                created_at=now,
                updated_at=now,
            )
        )

    def _canonical(self, events: Sequence[WorkEvent]) -> tuple[WorkItem, WorkContract]:
        """The Work and contract as created; the stream, not the projection, is the truth."""
        created = next(event for event in events if event.event_type is WorkEventType.WORK_CREATED)
        proposed = next(
            event for event in events if event.event_type is WorkEventType.CONTRACT_PROPOSED
        )
        return (
            WorkItem.model_validate(created.payload_json),
            WorkContract.model_validate(proposed.payload_json),
        )

    @staticmethod
    def _attempts(events: Sequence[WorkEvent], stage: str) -> int:
        """How many attempts this stage has started, so the ladder position is replay-stable."""
        run_ids = (
            str(event.payload_json["run_id"])
            for event in events
            if event.event_type is WorkEventType.STAGE_STARTED
            and event.payload_json["stage"] == stage
        )
        return len(tuple(dict.fromkeys(run_ids)))

    @staticmethod
    def _findings(events: Sequence[WorkEvent]) -> list[str]:
        """Every repair finding so far, so the next compose sees what it has to fix."""
        return [
            str(event.payload_json["detail"])
            for event in events
            if event.event_type is WorkEventType.OBSERVATION_RECORDED
            and event.payload_json.get("check") == "report_repair"
        ]

    async def _set(
        self,
        record: WorkRecord,
        status: str,
        *,
        report: dict[str, Any] | None = None,
    ) -> WorkRecord:
        """Write the record, merging profile_context so task_id and cycle survive."""
        profile_context = dict(record.profile_context)
        if report is not None:
            profile_context["report"] = report
        updated = record.model_copy(
            update={
                "status": status,
                "profile_context": profile_context,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await self._work_store.save_work(updated)
        return updated

    async def deliver(
        self,
        work_id: str,
        *,
        project_id: str,
        sink_version: int,
    ) -> tuple[WorkRecord, tuple[DeliveryReceipt, ...]]:
        """Deliver to the sink of that version; the last sink completes the Work."""
        record = await self._work_store.load_work(work_id, project_id=project_id)
        events = await self._work_store.read_events(work_id, project_id=project_id)
        work_item, contract = self._canonical(events)
        context = ReportContractContext.model_validate(contract.profile_context)
        archive = ReportArchive.model_validate(record.profile_context["report"]["archive"])
        sink = next(item for item in context.sinks if item.version == sink_version)
        action = self._sinks[sink.kind].action(
            project_id=work_item.project_id,
            work_id=work_item.id,
            sink=sink,
            archive=archive,
        )
        recorded = _recorded_delivery(record, sink_version)
        if recorded is not None:
            return record, (_receipt(project_id, work_id, sink_version, action, recorded),)
        body = self._artifacts.read(archive.report_ref, project_id=project_id).decode("utf-8")
        started = datetime.now(timezone.utc)
        delivery = await self._sinks[sink.kind].deliver(
            project_id=project_id,
            sink=sink,
            body=body,
            archive=archive,
        )
        receipt = DeliveryReceipt(
            action=action,
            result=ActionResult(
                project_id=project_id,
                action_id=deliver_action_id(work_id, sink_version=sink_version),
                status="succeeded" if delivery.observation["passed"] else "failed",
                external_ref=delivery.external_ref,
                evidence_refs=delivery.evidence_refs,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            ),
            observation={
                "action_id": deliver_action_id(work_id, sink_version=sink_version),
                **delivery.observation,
            },
        )
        await self._append(
            work_item,
            WorkEventType.EXECUTION_RECORDED,
            {
                "action": "report_delivered",
                "sink": sink.kind,
                "sink_version": sink.version,
                "external_ref": delivery.external_ref,
            },
        )
        await self._append(work_item, WorkEventType.OBSERVATION_RECORDED, receipt.observation)
        delivered = [
            *((record.profile_context.get("report") or {}).get("delivered") or []),
            {
                "sink_kind": sink.kind,
                "sink_version": sink.version,
                "external_ref": delivery.external_ref,
                "evidence_refs": list(delivery.evidence_refs),
                "observation": delivery.observation,
                "delivered_at": started.isoformat(),
            },
        ]
        record = await self._set(
            record,
            record.status,
            report={**record.profile_context["report"], "delivered": delivered},
        )
        if not delivery.observation["passed"]:
            return await self._block(
                record,
                work_item,
                "report_delivery_post_check_failed",
                str(delivery.observation["detail"]),
            ), (receipt,)
        if any(not _is_delivered(record, item.version) for item in context.sinks):
            return await self._ready(record, work_item, context, archive), (receipt,)
        verification = await self._profile.verify(
            work_item,
            contract,
            (context.report_criterion_id,),
            (receipt.result,),
        )
        await self._append(
            work_item,
            WorkEventType.VERIFICATION_RECORDED,
            verification.model_dump(mode="json"),
        )
        await self._append(work_item, WorkEventType.WORK_COMPLETED, {"run_id": work_item.id})
        return await self._set(record, _STATUS["complete"]), (receipt,)

    async def _append(
        self,
        work_item: WorkItem,
        event_type: WorkEventType,
        payload: dict[str, Any],
        *,
        actor_ref: str = "profile:report",
    ) -> None:
        events = await self._work_store.read_events(work_item.id, project_id=work_item.project_id)
        sequence = events[-1].sequence + 1 if events else 1
        await self._work_store.append_event(
            self._event(work_item, sequence, event_type, payload, actor_ref=actor_ref)
        )

    def _event(
        self,
        work_item: WorkItem,
        sequence: int,
        event_type: WorkEventType,
        payload: dict[str, Any],
        *,
        actor_ref: str = "profile:report",
    ) -> WorkEvent:
        return WorkEvent(
            id=str(uuid.uuid4()),
            project_id=work_item.project_id,
            work_id=work_item.id,
            sequence=sequence,
            event_type=event_type,
            actor_type="system",
            actor_ref=actor_ref,
            payload_json=payload,
            created_at=datetime.now(timezone.utc),
        )


def _is_delivered(record: WorkRecord, sink_version: int) -> bool:
    """Whether this Work already delivered that sink version (replay-safe, no I/O)."""
    delivered = (record.profile_context.get("report") or {}).get("delivered") or []
    return sink_version in {int(entry["sink_version"]) for entry in delivered}


def _recorded_delivery(record: WorkRecord, sink_version: int) -> dict[str, Any] | None:
    """The receipt of an earlier delivery of this version, so a replay posts nothing twice."""
    for entry in (record.profile_context.get("report") or {}).get("delivered") or []:
        if int(entry["sink_version"]) == sink_version:
            return entry
    return None


def _receipt(
    project_id: str,
    work_id: str,
    sink_version: int,
    action: ActionRequest,
    entry: dict[str, Any],
) -> DeliveryReceipt:
    """Rebuild the receipt of a delivery that already happened; no side effect is repeated."""
    delivered_at = datetime.fromisoformat(str(entry["delivered_at"]))
    action_id = deliver_action_id(work_id, sink_version=sink_version)
    return DeliveryReceipt(
        action=action,
        result=ActionResult(
            project_id=project_id,
            action_id=action_id,
            status="succeeded" if entry["observation"]["passed"] else "failed",
            external_ref=str(entry["external_ref"]),
            evidence_refs=tuple(entry["evidence_refs"]),
            started_at=delivered_at,
            completed_at=delivered_at,
        ),
        observation={"action_id": action_id, **entry["observation"]},
    )


__all__ = ["ReportLifecycle", "ReportOperator", "allowed_hosts"]
