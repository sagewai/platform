# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task definition, policy, defaults, and projection models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sagewai.artifacts.models import ArtifactRef
from sagewai.work.runtime import CapabilityGrant
from sagewai.work.tasks.schedule import validate_cron, validate_timezone


class TaskKind(str, Enum):
    BATCH = "batch"
    SCHEDULED = "scheduled"
    EVENT_DRIVEN = "event_driven"


class TaskOrigin(str, Enum):
    HUMAN = "human"
    SCHEDULE = "schedule"
    TRIGGER = "trigger"
    MONITOR = "monitor"
    AI_DECISION = "ai_decision"


class TaskStatus(str, Enum):
    PLANNING = "PLANNING"
    CLARIFYING = "CLARIFYING"
    PLAN_PROPOSED = "PLAN_PROPOSED"
    EXECUTING = "EXECUTING"
    ASSESSING = "ASSESSING"
    SCHEDULED = "SCHEDULED"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CONTROL_DEGRADED = "CONTROL_DEGRADED"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETE, TaskStatus.CANCELLED})


class AttentionOwner(str, Enum):
    USER = "user"
    SYSTEM = "system"
    EXTERNAL = "external"


class BoardColumn(str, Enum):
    INBOX = "inbox"
    NEEDS_YOU = "needs_you"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class GateMode(str, Enum):
    REQUIRE = "require"
    AUTO = "auto"
    BY_REVERSIBILITY = "by_reversibility"


_GATE_STRICTNESS = {GateMode.AUTO: 0, GateMode.BY_REVERSIBILITY: 1, GateMode.REQUIRE: 2}


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class RuntimeRef(str, Enum):
    CODEX = "codex"
    CLAUDE_ANALYSIS = "claude:analysis"
    CLAUDE_REVIEW = "claude:review"
    HARNESS_SIMPLE = "harness:simple"
    HARNESS_MEDIUM = "harness:medium"
    HARNESS_COMPLEX = "harness:complex"


class RoleAlias(str, Enum):
    PLANNER = "planner"
    DESIGNER = "designer"
    ANALYST = "analyst"
    IMPLEMENTER = "implementer"
    REPAIRER = "repairer"
    REVIEWER = "reviewer"
    ASSESSOR = "assessor"
    COMPOSER = "composer"


class Schedule(BaseModel):
    """Cron schedule evaluated in an IANA timezone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cron: str
    timezone: str
    active: bool = True

    @field_validator("cron")
    @classmethod
    def _validate_cron(cls, value: str) -> str:
        return validate_cron(value)

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        return validate_timezone(value)


class Budget(BaseModel):
    """Per-cycle limits; Codex attempts are counted, never priced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_works_per_cycle: int = Field(default=12, ge=1)
    max_stage_attempts_per_cycle: int = Field(default=60, ge=1)
    max_attempts_per_stage: int = Field(default=3, ge=1)
    max_replans: int = Field(default=2, ge=0)
    max_cycle_duration_seconds: int = Field(default=8 * 3600, ge=60)
    max_cycle_usd: Decimal = Field(default=Decimal("10.00"), ge=0)
    claude_max_budget_usd_per_attempt: Decimal = Field(default=Decimal("5.00"), gt=0)
    harness_max_tokens_per_attempt: int = Field(default=200_000, ge=1)
    harness_max_tool_calls_per_attempt: int = Field(default=60, ge=1)
    max_concurrent_works: Literal[1] = 1


class Authority(BaseModel):
    """Gate policy per gate class."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: GateMode = GateMode.REQUIRE
    merge: GateMode = GateMode.BY_REVERSIBILITY
    replan: GateMode = GateMode.BY_REVERSIBILITY
    deliver: GateMode = GateMode.BY_REVERSIBILITY

    @classmethod
    def for_kind(cls, kind: TaskKind) -> Authority:
        plan = GateMode.REQUIRE if kind is TaskKind.BATCH else GateMode.AUTO
        return cls(plan=plan)

    def tighten(self, floor: Authority) -> Authority:
        """Return the stricter of self and floor for every gate."""

        def pick(mine: GateMode, theirs: GateMode) -> GateMode:
            return mine if _GATE_STRICTNESS[mine] >= _GATE_STRICTNESS[theirs] else theirs

        return Authority(
            plan=pick(self.plan, floor.plan),
            merge=pick(self.merge, floor.merge),
            replan=pick(self.replan, floor.replan),
            deliver=pick(self.deliver, floor.deliver),
        )


class RoutingPolicy(BaseModel):
    """Runtime ladders per role; templates supply defaults in PR1b."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    roles: dict[RoleAlias, tuple[RuntimeRef, ...]] = Field(default_factory=dict)
    prefer_free_implementation: bool = False

    @field_validator("roles")
    @classmethod
    def _non_empty_ladders(
        cls, value: dict[RoleAlias, tuple[RuntimeRef, ...]]
    ) -> dict[RoleAlias, tuple[RuntimeRef, ...]]:
        for role, ladder in value.items():
            if not ladder:
                raise ValueError(f"ladder for {role.value} cannot be empty")
        return value


class ExecutionRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route: Literal["local", "fleet"] = "local"
    fleet_org_id: str | None = None

    @model_validator(mode="after")
    def _fleet_requires_org(self) -> ExecutionRoute:
        if self.route == "fleet" and not self.fleet_org_id:
            raise ValueError("fleet execution requires fleet_org_id")
        if self.route == "local" and self.fleet_org_id is not None:
            raise ValueError("local execution cannot name a Fleet organization")
        return self


class SoftwareTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["software"] = "software"
    repository_path: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    default_branch: str = Field(default="main", min_length=1)
    verification_image: str = Field(min_length=1)
    verification_commands: tuple[str, ...] = Field(default=("just smoke",), min_length=1)

    def lease_key(self, project_id: str) -> str:
        return f"{project_id}:{self.owner}/{self.repo}:{self.default_branch}"


class Sink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["console", "github_issue"]
    issue_url: str | None = None
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _issue_sink_needs_url(self) -> Sink:
        if self.kind == "github_issue" and not self.issue_url:
            raise ValueError("github_issue sink requires issue_url")
        if self.kind == "console" and self.issue_url is not None:
            raise ValueError("console sink takes no issue_url")
        return self


class ReportTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["report"] = "report"
    sources: tuple[CapabilityGrant, ...] = ()
    sinks: tuple[Sink, ...] = ()
    required_sections: tuple[str, ...] = ()
    max_bytes: int = Field(default=200_000, ge=1)

    @model_validator(mode="after")
    def _ensure_console_sink(self) -> ReportTarget:
        if not any(sink.kind == "console" for sink in self.sinks):
            object.__setattr__(self, "sinks", (Sink(kind="console"), *self.sinks))
        return self


TaskTarget = Annotated[SoftwareTarget | ReportTarget, Field(discriminator="kind")]


class Task(BaseModel):
    """Immutable Task definition; changes create a new revision through the store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    kind: TaskKind
    origin: TaskOrigin
    origin_ref: str | None = None
    title: str = Field(min_length=1, max_length=200)
    brief_ref: ArtifactRef
    brief_summary: str = Field(min_length=1, max_length=2000)
    source_ref: str | None = None
    template_id: str = Field(min_length=1)
    template_version: str = Field(min_length=1)
    slots: dict[str, Any] = Field(default_factory=dict)
    profile: Literal["software", "report"]
    target: TaskTarget
    schedule: Schedule | None = None
    budget: Budget = Field(default_factory=Budget)
    authority: Authority
    routing: RoutingPolicy = Field(default_factory=RoutingPolicy)
    routing_version: int = Field(default=1, ge=1)
    execution: ExecutionRoute = Field(default_factory=ExecutionRoute)
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    retention_days: int | None = Field(default=None, ge=1)
    tracking_issue_url: str | None = None
    created_by: str = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def _consistency(self) -> Task:
        if self.brief_ref.project_id != self.project_id:
            raise ValueError("brief artifact belongs to a different project")
        if self.target.kind != self.profile:
            raise ValueError("target kind must match the profile")
        if isinstance(self.target, ReportTarget):
            for grant in self.target.sources:
                if grant.project_id != self.project_id:
                    raise ValueError("report source belongs to a different project")
        if self.kind is TaskKind.SCHEDULED and self.schedule is None:
            raise ValueError("scheduled tasks require a schedule")
        if self.kind is not TaskKind.SCHEDULED and self.schedule is not None:
            raise ValueError("only scheduled tasks carry a schedule")
        return self

    @property
    def repository_lease_key(self) -> str | None:
        if isinstance(self.target, SoftwareTarget):
            return self.target.lease_key(self.project_id)
        return None


class HarnessTier(BaseModel):
    """A local harness tier; a priced backend would need a price field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: str = Field(min_length=1)
    model: str = Field(min_length=1)


class TaskDefaults(BaseModel):
    """Project-level defaults the composer prefills and the coordinator reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str = Field(min_length=1)
    target: TaskTarget | None = None
    execution: ExecutionRoute = Field(default_factory=ExecutionRoute)
    timezone: str = "UTC"
    clarification_deadline_seconds: int = Field(default=4 * 3600, ge=60)
    routing: RoutingPolicy = Field(default_factory=RoutingPolicy)
    harness_tiers: dict[Literal["simple", "medium", "complex"], HarnessTier] = Field(
        default_factory=dict
    )
    decision_channels: tuple[str, ...] = ("console",)
    revision: int = Field(default=0, ge=0)

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        return validate_timezone(value)


class TaskTriggerSpec(BaseModel):
    """A versioned, admin-approved rule that turns external events into Tasks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    source: Literal["github_label"]
    filter: dict[str, str]
    template_id: str = Field(min_length=1)
    template_version: str = Field(min_length=1)
    slots: dict[str, Any] = Field(default_factory=dict)
    authority: Authority = Field(default_factory=Authority)
    enabled: bool = True

    @model_validator(mode="after")
    def _filter_matches_source(self) -> TaskTriggerSpec:
        if self.source == "github_label" and set(self.filter) != {"owner", "repo", "label"}:
            raise ValueError("a github_label trigger filters on owner, repo, and label")
        return self


class BudgetUsed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    works: int = 0
    attempts: int = 0
    replans: int = 0
    seconds: int = 0
    usd_actual: Decimal = Decimal("0")
    usd_reserved: Decimal = Decimal("0")
    usd_unknown: int = 0


class SpendTotals(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    usd_reserved: Decimal
    usd_actual: Decimal
    unknown_settlements: int
    reservations: int


class TaskRecord(BaseModel):
    """Mutable projection derived from Task events."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    project_id: str
    kind: TaskKind
    origin: TaskOrigin
    title: str
    profile: Literal["software", "report"]
    status: TaskStatus
    last_event_sequence: int
    board_column: BoardColumn = BoardColumn.INBOX
    attention_owner: AttentionOwner | None = None
    waiting_reason: str | None = None
    current_cycle: int = 0
    plan_version: int = 0
    pending_gate: str | None = None
    pending_questions: int = 0
    pending_material_questions: int = 0
    next_run_at: datetime | None = None
    lease_owner: str | None = None
    lease_epoch: int = 0
    lease_expires_at: datetime | None = None
    revision: int = 0
    budget_used: BudgetUsed = Field(default_factory=BudgetUsed)
    created_at: datetime
    updated_at: datetime
