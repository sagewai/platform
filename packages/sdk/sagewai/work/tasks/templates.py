# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Versioned Task templates with slots, derived from the Autopilot Blueprint vocabulary."""

from __future__ import annotations

from typing import Any, Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sagewai.work.tasks.models import (
    Authority,
    GateMode,
    RoleAlias,
    RuntimeRef,
    TaskKind,
)
from sagewai.work.tasks.schedule import InvalidCronError, validate_cron


class SlotValidationError(ValueError):
    def __init__(self, slot_name: str, message: str) -> None:
        super().__init__(f"slot {slot_name!r}: {message}")
        self.slot_name = slot_name


class Validator(Protocol):
    def __call__(self, value: Any, *, slot_name: str) -> Any: ...


class ValidatorRegistry:
    """Name-keyed validators so templates never ship Python callables."""

    def __init__(self) -> None:
        self._validators: dict[str, Validator] = {}

    def register(self, name: str, validator: Validator) -> None:
        if name in self._validators:
            raise ValueError(f"validator {name!r} already registered")
        self._validators[name] = validator

    def get(self, name: str) -> Validator:
        if name not in self._validators:
            raise KeyError(f"unknown validator {name!r}")
        return self._validators[name]


def validate_cron_slot(value: Any, *, slot_name: str) -> str:
    try:
        return validate_cron(value)
    except (InvalidCronError, TypeError) as exc:
        raise SlotValidationError(slot_name, str(exc)) from exc


def validate_url_list(value: Any, *, slot_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise SlotValidationError(slot_name, "must be a non-empty list")
    normalized: list[str] = []
    for item in value:
        parsed = urlparse(item) if isinstance(item, str) else None
        if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SlotValidationError(slot_name, f"invalid url: {item!r}")
        normalized.append(item)
    return normalized


def validate_non_empty_text(value: Any, *, slot_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SlotValidationError(slot_name, "must be non-empty text")
    return value.strip()


default_registry = ValidatorRegistry()
default_registry.register("cron", validate_cron_slot)
default_registry.register("url_list", validate_url_list)
default_registry.register("text", validate_non_empty_text)


class SlotSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type_: str
    description: str
    required: bool = True
    default: Any = None
    validator_name: str | None = None

    def validate_value(self, value: Any, *, slot_name: str, registry: ValidatorRegistry) -> Any:
        if value is None:
            if self.required:
                raise SlotValidationError(slot_name, "required slot missing")
            return self.default
        if self.validator_name is None:
            return value
        try:
            validator = registry.get(self.validator_name)
        except KeyError as exc:
            raise SlotValidationError(slot_name, f"unknown validator {self.validator_name!r}") from exc
        return validator(value, slot_name=slot_name)


class StepSkeleton(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    goal: str
    domain: Literal["ui", "backend", "data", "docs", "report"]
    depends_on: tuple[str, ...] = ()


class MatrixItemTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    statement: str
    verification_kind: Literal["deterministic", "policy"]


class ClarificationSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    text: str
    kind: Literal["choice", "text"] = "text"
    options: tuple[str, ...] = ()
    default: str | None = None
    defaultable: bool = True
    rationale: str = ""
    when: Literal["missing_slot", "short_brief"]
    slot: str | None = None


class TaskTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    category: str = Field(min_length=1)
    kind: TaskKind
    profile: Literal["software", "report"]
    example_goals: tuple[str, ...] = Field(min_length=4)
    required_slots: dict[str, SlotSpec] = Field(default_factory=dict)
    optional_slots: dict[str, SlotSpec] = Field(default_factory=dict)
    sources_required: tuple[str, ...] = ()
    sinks_default: tuple[str, ...] = ("console",)
    grant_level: Literal["read", "propose", "execute"] = "read"
    default_cron: str | None = None
    plan_skeleton: tuple[StepSkeleton, ...] = ()
    matrix_template: tuple[MatrixItemTemplate, ...] = ()
    clarifications: tuple[ClarificationSpec, ...] = ()
    roles: dict[RoleAlias, tuple[RuntimeRef, ...]] = Field(default_factory=dict)
    authority_floor: Authority = Field(default_factory=Authority)

    @field_validator("default_cron")
    @classmethod
    def _cron(cls, value: str | None) -> str | None:
        return None if value is None else validate_cron(value)

    @model_validator(mode="after")
    def _consistent(self) -> TaskTemplate:
        if set(self.required_slots) & set(self.optional_slots):
            raise ValueError("a slot cannot be both required and optional")
        for role, ladder in self.roles.items():
            if not ladder:
                raise ValueError(f"ladder for {role.value} cannot be empty")
        if self.kind is TaskKind.SCHEDULED and self.default_cron is None:
            raise ValueError("scheduled templates need a default cron")
        return self

    def all_slot_specs(self) -> dict[str, SlotSpec]:
        return {**self.required_slots, **self.optional_slots}


def validate_slots(template: TaskTemplate, values: dict[str, Any], registry: ValidatorRegistry) -> dict[str, Any]:
    """Validate and normalise slot values against a template."""
    out: dict[str, Any] = {}
    for name, spec in template.all_slot_specs().items():
        out[name] = spec.validate_value(values.get(name), slot_name=name, registry=registry)
    unknown = set(values) - set(out)
    if unknown:
        raise SlotValidationError(sorted(unknown)[0], "unknown slot")
    return out


SOFTWARE_DELIVERY = TaskTemplate(
    id="software_delivery",
    version="1",
    title="Software delivery",
    description="Build or change an application in a GitHub repository from a brief.",
    category="software",
    kind=TaskKind.BATCH,
    profile="software",
    example_goals=(
        "Build the web application described in this design document",
        "Add a pause control to the browser game and cover it with tests",
        "Implement the REST API and persistence described in the brief",
        "Fix the failing test suite and refactor the module it covers",
        "Add an expires_at field to pending requests and hide expired ones",
        "Ship the feature in this GitHub issue with deterministic tests",
    ),
    required_slots={
        "repository": SlotSpec(type_="str", description="Absolute path to the trusted checkout", validator_name="text"),
    },
    optional_slots={
        "design_required": SlotSpec(
            type_="bool",
            description="Ask for a design stage before implementation",
            required=False,
            default=False,
        ),
    },
    grant_level="execute",
    matrix_template=(
        MatrixItemTemplate(
            id="verification",
            statement="the repository's locked verification passes at the merged head",
            verification_kind="deterministic",
        ),
        MatrixItemTemplate(
            id="brief",
            statement="every requirement in the brief is present in the merged application",
            verification_kind="policy",
        ),
    ),
    clarifications=(
        ClarificationSpec(
            id="repository",
            text="Which repository should this Task change?",
            when="missing_slot",
            slot="repository",
            defaultable=False,
            rationale="A software Task cannot run without a trusted checkout.",
        ),
        ClarificationSpec(
            id="outcome",
            text="What outcome would make this Task done?",
            when="short_brief",
            default="The planner's interpretation of the brief, recorded as an assumption.",
            rationale="The brief is short; a stated outcome tightens the plan.",
        ),
    ),
    roles={
        RoleAlias.PLANNER: (RuntimeRef.CLAUDE_ANALYSIS,),
        RoleAlias.DESIGNER: (RuntimeRef.CLAUDE_ANALYSIS,),
        RoleAlias.ANALYST: (RuntimeRef.HARNESS_MEDIUM, RuntimeRef.CLAUDE_ANALYSIS),
        RoleAlias.IMPLEMENTER: (RuntimeRef.CODEX,),
        RoleAlias.REPAIRER: (RuntimeRef.CODEX,),
        RoleAlias.REVIEWER: (RuntimeRef.CLAUDE_REVIEW,),
        RoleAlias.ASSESSOR: (RuntimeRef.CLAUDE_REVIEW,),
    },
    authority_floor=Authority(plan=GateMode.REQUIRE),
)

SCHEDULED_RESEARCH_REPORT = TaskTemplate(
    id="scheduled_research_report",
    version="2",
    title="Scheduled research report",
    description="Read declared sources on a schedule and deliver a grounded report.",
    category="research",
    kind=TaskKind.SCHEDULED,
    profile="report",
    example_goals=(
        "Run daily research on 10 competitor websites, product launches, and agent pricing, and email me a summary each morning",
        "Every weekday at 9 AM scan the following 5 vendor blogs, product pages, and URL announcements and produce a digest",
        "Schedule a recurring job to research what my top vendors shipped each week",
        "Monitor these 8 news sources every morning and summarise AI-related headlines",
        "Create a weekly research loop that compiles a market landscape report from vendor sites",
        "Fetch and summarise the top 5 papers on arxiv daily",
    ),
    required_slots={
        "sources": SlotSpec(type_="list[str]", description="Source URLs to read", validator_name="url_list"),
    },
    optional_slots={
        "cron": SlotSpec(type_="str", description="Schedule", required=False, default="0 8 * * *", validator_name="cron"),
        "required_sections": SlotSpec(
            type_="list[str]",
            description="Headings the report must contain",
            required=False,
            default=["Summary"],
        ),
    },
    sources_required=("browser",),
    sinks_default=("console",),
    grant_level="read",
    default_cron="0 8 * * *",
    plan_skeleton=(
        StepSkeleton(id="report", title="Compose the report", goal="Read the declared sources and compose the report", domain="report"),
    ),
    matrix_template=(
        MatrixItemTemplate(
            id="grounded",
            statement="every claim in the report cites a source snapshot",
            verification_kind="policy",
        ),
    ),
    clarifications=(
        ClarificationSpec(
            id="sources",
            text="Which sources should the report read?",
            when="missing_slot",
            slot="sources",
            defaultable=False,
            rationale="A report needs declared, allow-listed sources.",
        ),
    ),
    roles={
        RoleAlias.PLANNER: (RuntimeRef.CLAUDE_ANALYSIS,),
        RoleAlias.COMPOSER: (RuntimeRef.HARNESS_MEDIUM, RuntimeRef.CLAUDE_ANALYSIS),
        RoleAlias.REVIEWER: (RuntimeRef.CLAUDE_REVIEW,),
        RoleAlias.ASSESSOR: (RuntimeRef.CLAUDE_ANALYSIS,),
    },
    authority_floor=Authority(plan=GateMode.AUTO),
)

CATALOGUE: dict[str, TaskTemplate] = {
    SOFTWARE_DELIVERY.id: SOFTWARE_DELIVERY,
    SCHEDULED_RESEARCH_REPORT.id: SCHEDULED_RESEARCH_REPORT,
}

RESERVED_TEMPLATE_IDS: tuple[str, ...] = ("event_triage", "batch_extract")


def get_template(template_id: str) -> TaskTemplate:
    if template_id not in CATALOGUE:
        raise KeyError(f"unknown template: {template_id}")
    return CATALOGUE[template_id]


__all__ = [
    "CATALOGUE",
    "RESERVED_TEMPLATE_IDS",
    "ClarificationSpec",
    "MatrixItemTemplate",
    "SlotSpec",
    "SlotValidationError",
    "StepSkeleton",
    "TaskTemplate",
    "ValidatorRegistry",
    "default_registry",
    "get_template",
    "validate_slots",
]
