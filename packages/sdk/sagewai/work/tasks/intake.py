# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic intake: route a brief to a template, extract a schedule, ask questions."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sagewai.work.tasks.models import SoftwareTarget, TaskDefaults, TaskKind
from sagewai.work.tasks.templates import CATALOGUE, ClarificationSpec, TaskTemplate

Band = Literal["auto_route", "picker", "synthesis"]

_STOPWORDS = frozenset(
    "a an and are as at be by for from in into is it me my of on or that the these this to "
    "with your our each all any using use via per".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Golden intake set calibration: _AUTO_MIN=0.36, _AUTO_MARGIN=0.12, _PICKER_MIN=0.18.
_AUTO_MIN = 0.36
_AUTO_MARGIN = 0.12
_PICKER_MIN = 0.18
_SHORT_BRIEF_WORDS = 25
_MAX_QUESTIONS = 3
# Slots only the project's target can fill: an answer cannot build a SoftwareTarget (section 5.1),
# so intake never asks for them and the preview carries the refusal ``create`` raises instead.
_TARGET_SLOTS = frozenset({"repository"})

_TIME_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
_SCHEDULE_HINT_RE = re.compile(
    r"\b(every|each)\b|\b(daily|nightly|hourly|weekly|weekdays?|mornings?|minutes?|hours?)\b|\bon\s+(mon|tues|wednes|thurs|fri|satur|sun)days?\b",
    re.IGNORECASE,
)


class ClarificationQuestion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    text: str
    kind: Literal["choice", "text"] = "text"
    options: tuple[str, ...] = ()
    default: str | None = None
    defaultable: bool = True
    rationale: str = ""
    attention_version: int = Field(default=1, ge=1)


class IntakeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str
    template_version: str
    band: Band
    confidence: float = Field(ge=0.0, le=1.0)
    candidates: tuple[str, ...]
    slots: dict[str, Any]
    cron: str | None
    timezone: str
    questions: tuple[ClarificationQuestion, ...]
    preview: str


def tokenize(text: str) -> list[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    return [token for token in tokens if len(token) >= 3 and token not in _STOPWORDS]


def score_template(brief_tokens: list[str], template: TaskTemplate) -> float:
    """Best overlap between the brief and any example goal, plus a category bonus."""
    if not brief_tokens:
        return 0.0
    brief = set(brief_tokens)
    best = 0.0
    for goal in template.example_goals:
        goal_tokens = set(tokenize(goal))
        if not goal_tokens:
            continue
        overlap = len(brief & goal_tokens)
        best = max(best, overlap / max(len(brief), 1))
    if template.category in brief:
        best = min(1.0, best + 0.1)
    return best


def _time_from_match(match: re.Match[str] | None) -> tuple[int, int] | None:
    if match is None:
        return 8, 0
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def extract_schedule(text: str) -> str | None:
    lowered = text.lower()
    time_match = _TIME_RE.search(lowered)
    time_parts = _time_from_match(time_match)
    if time_parts is None:
        return None
    hour, minute = time_parts
    if re.search(r"\b(every\s+weekday|weekdays)\b", lowered):
        return f"{minute} {hour} * * 1-5"
    if re.search(r"\bhourly\b|\bevery\s+hour\b", lowered):
        return "0 * * * *"
    if re.search(r"\bnightly\b|\bevery\s+night\b", lowered):
        return "0 2 * * *" if time_match is None else f"{minute} {hour} * * *"
    if re.search(r"\bweekly\b|\bevery\s+week\b|\beach\s+week\b", lowered):
        return f"{minute} {hour} * * 1"
    if re.search(r"\bdaily\b|\bevery\s+day\b|\beach\s+day\b|\bevery\s+morning\b|\beach\s+morning\b", lowered):
        return f"{minute} {hour} * * *"
    return None


def schedule_mentioned(text: str) -> bool:
    return _SCHEDULE_HINT_RE.search(text) is not None


def _question(spec: ClarificationSpec) -> ClarificationQuestion:
    return ClarificationQuestion(
        id=spec.id, text=spec.text, kind=spec.kind, options=spec.options, default=spec.default,
        defaultable=spec.defaultable, rationale=spec.rationale,
    )


def _candidate_titles(candidates: tuple[str, ...]) -> str:
    return " and ".join(CATALOGUE[candidate].title for candidate in candidates)


def _preview(
    template: TaskTemplate,
    cron: str | None,
    questions: tuple[ClarificationQuestion, ...],
    *,
    blocked: str,
) -> str:
    reads = {
        "software": "the trusted repository checkout and the brief",
        "report": "the declared sources through allow-listed browsing",
    }[template.profile]
    changes = {
        "software": "creates issues, branches, and pull requests in the target repository",
        "report": "writes a report artifact and, when configured, posts it to a GitHub issue",
    }[template.profile]
    spend = "at most the Task budget; Claude attempts carry a dollar cap, Codex attempts are counted"
    approvals = (
        "a plan approval before execution" if template.authority_floor.plan.value == "require" else "no plan approval"
    ) + "; irreversible actions always need a project admin"
    schedule = f" It runs on schedule {cron}." if cron else ""
    if len(questions) == 1:
        asks = " It will first ask 1 question."
    elif questions:
        asks = f" It will first ask {len(questions)} questions."
    else:
        asks = ""
    return (
        f"This Task will read {reads}. It {changes}. It spends {spend}. "
        f"It asks for {approvals}.{schedule}{asks}{blocked}"
    )


def route(brief: str, defaults: TaskDefaults) -> IntakeResult:
    """Deterministically route a brief to a template with a confidence band."""
    brief_tokens = tokenize(brief)
    scored = sorted(
        ((score_template(brief_tokens, template), template_id) for template_id, template in CATALOGUE.items()),
        reverse=True,
    )
    top_score, top_id = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if len(brief_tokens) >= 4 and top_score >= _AUTO_MIN and top_score - second_score >= _AUTO_MARGIN:
        band: Band = "auto_route"
        template = CATALOGUE[top_id]
    elif top_score >= _PICKER_MIN:
        band = "picker"
        template = CATALOGUE[top_id]
    else:
        band = "synthesis"
        template = CATALOGUE[top_id]
    candidates = tuple(template_id for _, template_id in scored[:2])

    slots: dict[str, Any] = {}
    if template.profile == "software" and isinstance(defaults.target, SoftwareTarget):
        slots["repository"] = defaults.target.repository_path
    blocked = (
        ""
        if defaults.target is not None and defaults.target.kind == template.profile
        else (
            f" Creation will fail: project {defaults.project_id} has no {template.profile} "
            f"target for template {template.id}."
        )
    )
    cron = extract_schedule(brief) if template.kind is TaskKind.SCHEDULED else None
    questions: list[ClarificationQuestion] = []
    if template.kind is TaskKind.SCHEDULED:
        if cron is None and schedule_mentioned(brief):
            questions.append(
                ClarificationQuestion(
                    id="schedule",
                    text="Which schedule should this run on? A phrase like 'weekdays at 9' or a cron expression.",
                    kind="text",
                    default=template.default_cron,
                    defaultable=True,
                    rationale="The schedule phrase in the brief was not understood.",
                )
            )
        cron = cron or template.default_cron
        slots["cron"] = cron

    short = len(brief.split()) < _SHORT_BRIEF_WORDS
    for spec in template.clarifications:
        if spec.when == "missing_slot" and spec.slot is not None and spec.slot not in slots:
            if spec.slot in _TARGET_SLOTS:
                continue
            questions.append(_question(spec))
        elif spec.when == "short_brief" and band == "synthesis" and short:
            questions.append(_question(spec))
    questions = sorted(questions, key=lambda question: question.defaultable)[:_MAX_QUESTIONS]
    preview = _preview(template, cron, tuple(questions), blocked=blocked)
    if band == "synthesis":
        preview = (
            f"The brief did not clearly match a template; best guess is {template.title}; "
            f"candidates are {_candidate_titles(candidates)}; the template must be confirmed "
            f"before creation. {preview}"
        )
    elif band == "picker":
        preview = (
            f"Best guess is {template.title}; candidates are {_candidate_titles(candidates)}; "
            f"use this as a suggestion. {preview}"
        )

    return IntakeResult(
        template_id=template.id,
        template_version=template.version,
        band=band,
        confidence=round(min(top_score, 1.0), 3),
        candidates=candidates,
        slots=slots,
        cron=cron,
        timezone=defaults.timezone,
        questions=tuple(questions),
        preview=preview,
    )


__all__ = [
    "Band",
    "ClarificationQuestion",
    "IntakeResult",
    "extract_schedule",
    "route",
    "schedule_mentioned",
    "score_template",
    "tokenize",
]
