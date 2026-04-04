# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Event hook factories for wiring notifications into agents.

These hooks listen for agent lifecycle events (budget warnings, workflow
failures, etc.) and dispatch them through the NotificationService.

Usage::

    from sagewai.notifications.hooks import create_budget_notification_hook

    hook = create_budget_notification_hook(notification_service, project_id="t1")
    agent._event_listeners.append(hook)
"""

from __future__ import annotations

from typing import Any

from sagewai.notifications.service import NotificationService


def create_budget_notification_hook(
    notification_service: NotificationService,
    project_id: str | None = None,
):
    """Return an async event hook that sends budget notifications.

    Compatible with BaseAgent._event_listeners signature: (event, data).
    """

    async def hook(event: Any, data: dict[str, Any]) -> None:
        ev = event.value if hasattr(event, "value") else str(event)
        agent_name = data.get("agent_name", "unknown")

        if ev == "budget_warning":
            await notification_service.notify(
                trigger="budget_warning",
                title=f"Budget warning: {agent_name}",
                body=f"Agent approaching budget limit. {data.get('reason', '')}",
                severity="warning",
                project_id=project_id,
                agent_name=agent_name,
            )
        elif ev == "budget_exceeded":
            await notification_service.notify(
                trigger="budget_exceeded",
                title=f"Budget exceeded: {agent_name}",
                body=f"Agent blocked due to budget limit. {data.get('reason', '')}",
                severity="critical",
                project_id=project_id,
                agent_name=agent_name,
            )
        elif ev == "budget_throttled":
            await notification_service.notify(
                trigger="budget_throttled",
                title=f"Budget throttled: {agent_name}",
                body=f"Agent throttled due to budget limit. {data.get('reason', '')}",
                severity="warning",
                project_id=project_id,
                agent_name=agent_name,
            )

    return hook


def create_workflow_notification_hook(
    notification_service: NotificationService,
    project_id: str | None = None,
):
    """Return an async event hook that sends workflow failure notifications.

    Compatible with BaseAgent._event_listeners signature: (event, data).
    """

    async def hook(event: Any, data: dict[str, Any]) -> None:
        ev = event.value if hasattr(event, "value") else str(event)

        if ev == "run_error":
            await notification_service.notify(
                trigger="workflow_failed",
                title=f"Workflow failed: {data.get('agent_name', 'unknown')}",
                body=f"Error: {data.get('error', 'unknown error')}",
                severity="critical",
                project_id=project_id,
                agent_name=data.get("agent_name"),
            )
        elif ev == "approval_requested":
            await notification_service.notify(
                trigger="approval_requested",
                title=f"Approval requested: {data.get('workflow_name', 'unknown')}",
                body=f"Workflow requires manual approval. {data.get('reason', '')}",
                severity="info",
                project_id=project_id,
                agent_name=data.get("agent_name"),
            )

    return hook
