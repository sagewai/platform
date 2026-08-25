# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Append-only Work-domain events."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict


class WorkEventType(str, Enum):
    """Initial durable Work-domain event vocabulary."""

    WORK_CREATED = "WORK_CREATED"
    CONTRACT_PROPOSED = "CONTRACT_PROPOSED"
    CONTRACT_ACCEPTED = "CONTRACT_ACCEPTED"
    ASSUMPTION_RECORDED = "ASSUMPTION_RECORDED"
    STAGE_STARTED = "STAGE_STARTED"
    STAGE_COMPLETED = "STAGE_COMPLETED"
    EXECUTION_RECORDED = "EXECUTION_RECORDED"
    VERIFICATION_RECORDED = "VERIFICATION_RECORDED"
    REVIEW_RECORDED = "REVIEW_RECORDED"
    GATE_REQUESTED = "GATE_REQUESTED"
    GATE_DECIDED = "GATE_DECIDED"
    RELEASE_CREATED = "RELEASE_CREATED"
    DEPLOYMENT_RECORDED = "DEPLOYMENT_RECORDED"
    OBSERVATION_RECORDED = "OBSERVATION_RECORDED"
    OPERATOR_DISCIPLINE_RECORDED = "OPERATOR_DISCIPLINE_RECORDED"
    CONTROL_DEGRADED = "CONTROL_DEGRADED"
    CONTROL_RESTORED = "CONTROL_RESTORED"
    ROLLBACK_RECORDED = "ROLLBACK_RECORDED"
    TRIAGE_CREATED = "TRIAGE_CREATED"
    WORK_BLOCKED = "WORK_BLOCKED"
    WORK_COMPLETED = "WORK_COMPLETED"


class WorkEvent(BaseModel):
    """One immutable business event in a WorkItem stream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str | None
    work_id: str
    sequence: int
    event_type: WorkEventType
    actor_type: str
    actor_ref: str | None
    payload_json: dict[str, Any]
    created_at: datetime
