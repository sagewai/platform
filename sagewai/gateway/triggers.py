# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Trigger configuration and event dispatch for the event system."""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent

logger = logging.getLogger(__name__)


class Strategy(str, Enum):
    WEBHOOK = "webhook"
    LISTENER = "listener"
    POLLER = "poller"


class IncomingEvent(BaseModel):
    """A normalized event from any source."""

    source: str
    event_type: str
    channel: str | None = None
    sender: str | None = None
    payload: dict[str, Any]
    timestamp: str


class EventFilter(BaseModel):
    """Filter for matching incoming events."""

    channels: list[str] | None = None
    event_types: list[str] | None = None
    senders: list[str] | None = None
    keywords: list[str] | None = None
    to: list[str] | None = None

    def matches(self, event: IncomingEvent) -> bool:
        """Check if an event matches this filter."""
        if self.channels and event.channel not in self.channels:
            return False
        if self.event_types and event.event_type not in self.event_types:
            return False
        if self.senders and event.sender not in self.senders:
            return False
        if self.to:
            recipient = event.payload.get("to", "")
            if not any(t in str(recipient) for t in self.to):
                return False
        if self.keywords:
            text = str(event.payload.get("text", event.payload.get("message", "")))
            if not any(kw.lower() in text.lower() for kw in self.keywords):
                return False
        return True


class TriggerSpec(BaseModel):
    """Declarative mapping from events to agent actions."""

    source: str
    strategy: Strategy
    poll_interval: timedelta | None = None
    filter: EventFilter
    target: str
    action: str  # "chat", "run_workflow", "execute_tool"
    context: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class AgentResolver(ABC):
    """Resolves agent/workflow names to instances."""

    @abstractmethod
    async def resolve(self, target: str) -> BaseAgent: ...


# Callback type for submitting workflow runs from triggers.
# Signature: (yaml_string, message) -> dict with run_id etc.
WorkflowSubmitter = Callable[[str, str], Awaitable[dict[str, Any]]]


class TriggerStore(ABC):
    """Persists trigger configurations."""

    @abstractmethod
    async def save(self, trigger_id: str, trigger: TriggerSpec) -> None: ...

    @abstractmethod
    async def get(self, trigger_id: str) -> TriggerSpec | None: ...

    @abstractmethod
    async def list_all(self) -> list[tuple[str, TriggerSpec]]: ...

    @abstractmethod
    async def delete(self, trigger_id: str) -> None: ...


class InMemoryTriggerStore(TriggerStore):
    def __init__(self) -> None:
        self._triggers: dict[str, TriggerSpec] = {}

    async def save(self, trigger_id: str, trigger: TriggerSpec) -> None:
        self._triggers[trigger_id] = trigger

    async def get(self, trigger_id: str) -> TriggerSpec | None:
        return self._triggers.get(trigger_id)

    async def list_all(self) -> list[tuple[str, TriggerSpec]]:
        return list(self._triggers.items())

    async def delete(self, trigger_id: str) -> None:
        self._triggers.pop(trigger_id, None)


class TriggerManager:
    """Manages trigger registration, matching, and dispatch."""

    def __init__(
        self,
        agent_resolver: AgentResolver,
        trigger_store: TriggerStore | None = None,
        workflow_submitter: WorkflowSubmitter | None = None,
    ) -> None:
        self._resolver = agent_resolver
        self._store = trigger_store or InMemoryTriggerStore()
        self._triggers: dict[str, TriggerSpec] = {}
        self._workflow_submitter = workflow_submitter

    async def register(self, trigger: TriggerSpec) -> str:
        trigger_id = uuid.uuid4().hex[:12]
        self._triggers[trigger_id] = trigger
        await self._store.save(trigger_id, trigger)
        return trigger_id

    async def remove(self, trigger_id: str) -> None:
        self._triggers.pop(trigger_id, None)
        await self._store.delete(trigger_id)

    async def enable(self, trigger_id: str) -> None:
        if trigger_id in self._triggers:
            self._triggers[trigger_id].enabled = True

    async def disable(self, trigger_id: str) -> None:
        if trigger_id in self._triggers:
            self._triggers[trigger_id].enabled = False

    async def list(self) -> list[tuple[str, TriggerSpec]]:
        return list(self._triggers.items())

    async def dispatch(self, event: IncomingEvent) -> None:
        """Match event against triggers and dispatch to target agent."""
        for tid, trigger in self._triggers.items():
            if not trigger.enabled:
                continue
            if trigger.source != event.source:
                continue
            if not trigger.filter.matches(event):
                continue
            try:
                msg = event.payload.get(
                    "text", event.payload.get("message", str(event.payload))
                )

                if trigger.action == "chat":
                    agent = await self._resolver.resolve(trigger.target)
                    await agent.chat(str(msg))

                elif trigger.action == "run_workflow":
                    yaml_str = trigger.context.get("yaml", "")
                    if not yaml_str:
                        logger.error(
                            "Trigger %s: run_workflow requires context.yaml", tid
                        )
                        continue
                    if self._workflow_submitter is None:
                        logger.error(
                            "Trigger %s: no workflow_submitter configured", tid
                        )
                        continue
                    result = await self._workflow_submitter(yaml_str, str(msg))
                    logger.info(
                        "Trigger %s: submitted workflow run %s",
                        tid,
                        result.get("run_id", "?"),
                    )

                elif trigger.action == "execute_tool":
                    tool_name = trigger.context.get("tool_name", "")
                    if not tool_name:
                        logger.error(
                            "Trigger %s: execute_tool requires context.tool_name", tid
                        )
                        continue
                    tool_args = trigger.context.get("tool_arguments", {})
                    agent = await self._resolver.resolve(trigger.target)
                    result = await agent.execute_tool(tool_name, tool_args)
                    if result.error:
                        logger.warning(
                            "Trigger %s: tool %s returned error: %s",
                            tid,
                            tool_name,
                            result.error,
                        )

                else:
                    logger.warning(
                        "Trigger %s: unknown action %r", tid, trigger.action
                    )

            except Exception:
                logger.exception("Failed to dispatch event to %s", trigger.target)

    async def load_from_store(self) -> None:
        """Load triggers from persistent store into memory."""
        items = await self._store.list_all()
        for tid, trigger in items:
            self._triggers[tid] = trigger
