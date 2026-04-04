# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Generic agent lifecycle events.

These are lightweight, AG-UI-agnostic events emitted by BaseAgent during
execution. An adapter (e.g. the AG-UI middleware) translates them into
protocol-specific event objects.

Each event is identified by a string constant and carries a ``dict[str, Any]``
payload whose keys depend on the event type.
"""

from __future__ import annotations

from enum import Enum


class AgentEvent(str, Enum):
    """Lifecycle events emitted by BaseAgent."""

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    RUN_ERROR = "run_error"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    TEXT_MESSAGE_START = "text_message_start"
    TEXT_MESSAGE_CONTENT = "text_message_content"
    TEXT_MESSAGE_END = "text_message_end"
    LLM_CALL_FINISHED = "llm_call_finished"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    TOOL_CALL_RESULT = "tool_call_result"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"
    RUN_CANCELLED = "run_cancelled"
    PROMPT_LOGGED = "prompt_logged"
    HEALTH_CHANGED = "health_changed"
    ROUTE_SELECTED = "route_selected"
    PLAN_CREATED = "plan_created"
    PLAN_REVISED = "plan_revised"
    GUARDRAIL_ESCALATION = "guardrail_escalation"
    CONTEXT_COMPACTED = "context_compacted"
    SESSION_SAVED = "session_saved"
    SESSION_RESUMED = "session_resumed"
    MEMORY_EXTRACTED = "memory_extracted"

    # Approval events
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"

    # Worker events
    WORKER_STARTED = "worker_started"
    WORKER_STOPPED = "worker_stopped"
    WORKFLOW_CLAIMED = "workflow_claimed"
    WORKFLOW_RELEASED = "workflow_released"

    # Progress events
    WORKFLOW_PROGRESS = "workflow_progress"

    # Budget events
    BUDGET_WARNING = "budget_warning"
    BUDGET_THROTTLED = "budget_throttled"
    BUDGET_EXCEEDED = "budget_exceeded"

    # Permission events
    PERMISSION_DENIED = "permission_denied"

    # Compaction events
    COMPACTION_TRIGGERED = "compaction_triggered"

    # Streaming lifecycle
    MESSAGE_START = "message_start"
    MESSAGE_STOP = "message_stop"
