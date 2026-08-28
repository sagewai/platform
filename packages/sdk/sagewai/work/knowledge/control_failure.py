# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Project knowledge derived from durable control failures."""

from __future__ import annotations

from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.knowledge.models import KnowledgeItem, KnowledgeKind

CONTROL_FAILURE_IMPORTANCE = 90


def control_failure_finding(event: WorkEvent) -> KnowledgeItem:
    """Build the project-level finding identified by a degradation event."""

    if event.event_type is not WorkEventType.CONTROL_DEGRADED:
        raise ValueError("control failure finding requires a CONTROL_DEGRADED event")
    if event.project_id is None:
        raise ValueError("control failure finding requires a project-scoped event")

    failed_preconditions = tuple(
        str(value) for value in event.payload_json.get("failed_preconditions", ())
    )
    if not failed_preconditions:
        raise ValueError("control failure finding requires failed precondition ids")

    evidence_refs = tuple(
        dict.fromkeys(str(value) for value in event.payload_json.get("evidence_refs", ()))
    )
    details = str(event.payload_json.get("details", "")).strip()
    statement = (
        f"Control failure for preconditions {', '.join(failed_preconditions)} "
        f"during work {event.work_id}."
    )
    if details:
        statement = f"{statement} Details: {details}."

    return KnowledgeItem(
        id=f"{event.id}:control-failure",
        project_id=event.project_id,
        work_id=None,
        kind=KnowledgeKind.FINDING,
        statement=statement,
        source_refs=(f"work-event://{event.id}", *evidence_refs),
        factness_score=100,
        importance_score=CONTROL_FAILURE_IMPORTANCE,
        created_by=event.actor_ref or event.actor_type,
        created_at=event.created_at,
    )
