# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task definition, projection, and policy models."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from sagewai.artifacts.models import ArtifactRef
from sagewai.work.runtime import CapabilityGrant
from sagewai.work.tasks.models import (
    Authority,
    Budget,
    BudgetUsed,
    ExecutionRoute,
    GateMode,
    ReportTarget,
    RoutingPolicy,
    Schedule,
    Sink,
    SoftwareTarget,
    Task,
    TaskDefaults,
    TaskKind,
    TaskOrigin,
    TaskRecord,
    TaskStatus,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _brief(project_id: str = "project-a") -> ArtifactRef:
    return ArtifactRef(
        project_id=project_id,
        digest="sha256:" + "a" * 64,
        media_type="text/markdown",
        size_bytes=12,
        storage_ref="artifact://sha256:" + "a" * 64,
        created_at=NOW,
        created_by="test",
    )


def _software_target() -> SoftwareTarget:
    return SoftwareTarget(
        repository_path="/tmp/repo",
        owner="sagewai",
        repo="platform",
        default_branch="main",
        verification_image="sha256:" + "b" * 64,
    )


def _task(**updates) -> Task:
    values = dict(
        id="task-1",
        project_id="project-a",
        kind=TaskKind.BATCH,
        origin=TaskOrigin.HUMAN,
        title="Build the thing",
        brief_ref=_brief(),
        brief_summary="Build the thing",
        template_id="software_delivery",
        template_version="1",
        profile="software",
        target=_software_target(),
        schedule=None,
        budget=Budget(),
        authority=Authority.for_kind(TaskKind.BATCH),
        routing=RoutingPolicy(),
        execution=ExecutionRoute(route="local"),
        created_by="arda",
        created_at=NOW,
    )
    values.update(updates)
    return Task.model_validate(values)


def test_task_requires_project_and_matching_target_profile() -> None:
    assert _task().repository_lease_key == "project-a:sagewai/platform:main"
    with pytest.raises(ValidationError):
        _task(project_id="")
    with pytest.raises(ValidationError):
        _task(profile="report")
    with pytest.raises(ValidationError):
        _task(brief_ref=_brief("project-b"))


def test_scheduled_task_requires_schedule_and_others_forbid_it() -> None:
    with pytest.raises(ValidationError):
        _task(kind=TaskKind.SCHEDULED)
    scheduled = _task(
        kind=TaskKind.SCHEDULED,
        schedule=Schedule(cron="0 8 * * *", timezone="Europe/Berlin"),
        authority=Authority.for_kind(TaskKind.SCHEDULED),
    )
    assert scheduled.schedule is not None and scheduled.schedule.active
    with pytest.raises(ValidationError):
        _task(schedule=Schedule(cron="0 8 * * *", timezone="UTC"))
    with pytest.raises(ValidationError):
        Schedule(cron="0 8 * *", timezone="UTC")
    with pytest.raises(ValidationError):
        Schedule(cron="0 8 * * *", timezone="Nowhere/City")


def test_report_target_requires_console_sink_and_source_scope() -> None:
    grant = CapabilityGrant(
        project_id="project-a", name="cli:gh", kind="cli", scope={}, permissions=("read",)
    )
    target = ReportTarget(
        sources=(grant,),
        sinks=(Sink(kind="github_issue", issue_url="https://github.com/o/r/issues/1", version=2),),
        required_sections=("Summary",),
    )
    assert [sink.kind for sink in target.sinks] == ["console", "github_issue"]
    with pytest.raises(ValidationError):
        Sink(kind="github_issue")
    task = _task(profile="report", target=target)
    assert task.repository_lease_key is None
    foreign = CapabilityGrant(
        project_id="project-b", name="cli:gh", kind="cli", scope={}, permissions=("read",)
    )
    with pytest.raises(ValidationError):
        _task(profile="report", target=ReportTarget(sources=(foreign,), required_sections=("Summary",)))


def test_report_target_requires_distinct_sink_versions() -> None:
    with pytest.raises(ValidationError, match="each report sink needs its own version"):
        ReportTarget(
            sinks=(
                Sink(
                    kind="github_issue",
                    issue_url="https://github.com/o/r/issues/1",
                ),
            )
        )
    target = ReportTarget(
        sinks=(
            Sink(
                kind="github_issue",
                issue_url="https://github.com/o/r/issues/1",
                version=2,
            ),
        )
    )
    assert [(sink.kind, sink.version) for sink in target.sinks] == [
        ("console", 1),
        ("github_issue", 2),
    ]


def test_budget_defaults_and_bounds() -> None:
    budget = Budget()
    assert budget.max_works_per_cycle == 12
    assert budget.max_attempts_per_stage == 3
    assert budget.max_cycle_usd == Decimal("10.00")
    assert budget.max_concurrent_works == 1
    with pytest.raises(ValidationError):
        Budget(max_concurrent_works=2)
    with pytest.raises(ValidationError):
        Budget(max_cycle_usd=Decimal("-1"))


def test_authority_defaults_by_kind_and_tighten() -> None:
    batch = Authority.for_kind(TaskKind.BATCH)
    assert batch.plan is GateMode.REQUIRE
    assert batch.merge is GateMode.BY_REVERSIBILITY
    scheduled = Authority.for_kind(TaskKind.SCHEDULED)
    assert scheduled.plan is GateMode.AUTO
    tightened = Authority(plan=GateMode.AUTO, merge=GateMode.AUTO).tighten(
        Authority(plan=GateMode.REQUIRE, merge=GateMode.BY_REVERSIBILITY)
    )
    assert tightened.plan is GateMode.REQUIRE
    assert tightened.merge is GateMode.BY_REVERSIBILITY


def test_defaults_and_record_round_trip() -> None:
    defaults = TaskDefaults(project_id="project-a")
    assert defaults.timezone == "UTC"
    assert defaults.clarification_deadline_seconds == 4 * 3600
    assert defaults.decision_channels == ("console",)
    record = TaskRecord(
        task_id="task-1",
        project_id="project-a",
        kind=TaskKind.BATCH,
        origin=TaskOrigin.HUMAN,
        title="Build the thing",
        profile="software",
        status=TaskStatus.PLANNING,
        last_event_sequence=0,
        created_at=NOW,
        updated_at=NOW,
    )
    assert record.board_column.value == "inbox"
    assert record.budget_used == BudgetUsed()
    assert record.last_event_sequence == 0
    assert record.revision == 0
