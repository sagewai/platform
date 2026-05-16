# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""AG-UI FastAPI integration — reusable router for serving AG-UI event streams.

Usage in any sagewai app::

    from sagewai.integrations.fastapi import AGUIRouter

    agui = AGUIRouter(prefix="/agui")

    @app.on_event("startup")
    async def startup():
        agui.register_agent(my_agent)

    app.include_router(agui.router)

This gives you:
- ``POST /agui/runs`` — start an agent run, returns SSE stream of AG-UI events
- ``WS /agui/ws`` — WebSocket endpoint streaming AG-UI events
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from sagewai.core.base import BaseAgent
from sagewai.core.events import AgentEvent
from sagewai.protocols.agui.events import (
    BaseEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    """Request body for starting an agent run."""

    message: str
    thread_id: str | None = Field(default=None, alias="threadId")
    agent_name: str | None = Field(default=None, alias="agentName")

    model_config = {"populate_by_name": True}


def _translate_event(
    event: AgentEvent,
    data: dict[str, Any],
    thread_id: str,
    run_id: str,
) -> BaseEvent | None:
    """Translate a generic AgentEvent to an AG-UI protocol event."""
    mapping: dict[AgentEvent, type[BaseEvent] | None] = {
        AgentEvent.RUN_STARTED: None,  # Handled separately at run start
        AgentEvent.RUN_FINISHED: None,  # Handled separately at run end
        AgentEvent.RUN_ERROR: None,  # Handled separately
    }
    if event in mapping:
        return None

    if event == AgentEvent.STEP_STARTED:
        return StepStartedEvent(step_name=data.get("step", ""))
    if event == AgentEvent.STEP_FINISHED:
        return StepFinishedEvent(step_name=data.get("step", ""))
    if event == AgentEvent.TEXT_MESSAGE_START:
        return TextMessageStartEvent(message_id=data.get("message_id", ""))
    if event == AgentEvent.TEXT_MESSAGE_CONTENT:
        return TextMessageContentEvent(
            message_id=data.get("message_id", ""),
            delta=data.get("delta", ""),
        )
    if event == AgentEvent.TEXT_MESSAGE_END:
        return TextMessageEndEvent(message_id=data.get("message_id", ""))
    if event == AgentEvent.TOOL_CALL_START:
        return ToolCallStartEvent(
            tool_call_id=data.get("tool_call_id", ""),
            tool_call_name=data.get("tool_name", ""),
        )
    if event == AgentEvent.TOOL_CALL_END:
        return ToolCallEndEvent(tool_call_id=data.get("tool_call_id", ""))
    if event == AgentEvent.TOOL_CALL_RESULT:
        return ToolCallResultEvent(
            message_id=f"result_{data.get('tool_call_id', '')}",
            tool_call_id=data.get("tool_call_id", ""),
            content=data.get("content", ""),
        )
    return None


class AGUIRouter:
    """Reusable FastAPI router that serves AG-UI event streams.

    Translates BaseAgent lifecycle hooks into AG-UI protocol events
    and streams them via SSE or WebSocket.
    """

    def __init__(self, prefix: str = "/agui") -> None:
        self.router = APIRouter(prefix=prefix, tags=["ag-ui"])
        self._agents: dict[str, BaseAgent] = {}
        self._setup_routes()

    def register_agent(self, agent: BaseAgent) -> None:
        """Register an agent for AG-UI event streaming."""
        self._agents[agent.config.name] = agent

    def _get_agent(self, name: str | None) -> BaseAgent:
        """Resolve agent by name, defaulting to the first registered."""
        if name and name in self._agents:
            return self._agents[name]
        if self._agents:
            return next(iter(self._agents.values()))
        raise ValueError("No agents registered with AGUIRouter")

    def _setup_routes(self) -> None:
        @self.router.post("/runs")
        async def start_run(request: RunRequest) -> StreamingResponse:
            agent = self._get_agent(request.agent_name)
            thread_id = request.thread_id or str(uuid.uuid4())
            run_id = str(uuid.uuid4())

            async def event_stream() -> AsyncGenerator[str, None]:
                queue: asyncio.Queue[BaseEvent | None] = asyncio.Queue()

                async def on_event(event: AgentEvent, data: dict[str, Any]) -> None:
                    translated = _translate_event(event, data, thread_id, run_id)
                    if translated:
                        await queue.put(translated)

                agent.on_event(on_event)

                # Emit RUN_STARTED
                start_event = RunStartedEvent(
                    thread_id=thread_id, run_id=run_id, input={"message": request.message}
                )
                data = start_event.model_dump_json(by_alias=True)
                yield f"event: {start_event.type.value}\ndata: {data}\n\n"

                # Run agent in background task
                run_task = asyncio.create_task(agent.chat(request.message))
                error_occurred = False

                try:
                    while not run_task.done() or not queue.empty():
                        try:
                            agui_event = await asyncio.wait_for(queue.get(), timeout=0.1)
                        except asyncio.TimeoutError:
                            continue
                        if agui_event is None:
                            break
                        data = agui_event.model_dump_json(by_alias=True)
                        yield f"event: {agui_event.type.value}\ndata: {data}\n\n"

                    # Drain remaining events
                    while not queue.empty():
                        agui_event = queue.get_nowait()
                        if agui_event is None:
                            break
                        data = agui_event.model_dump_json(by_alias=True)
                        yield f"event: {agui_event.type.value}\ndata: {data}\n\n"

                    # Check for exception
                    if run_task.done() and run_task.exception():
                        exc = run_task.exception()
                        error_occurred = True
                        err_event = RunErrorEvent(message=str(exc))
                        data = err_event.model_dump_json(by_alias=True)
                        yield f"event: {err_event.type.value}\ndata: {data}\n\n"

                except Exception as exc:
                    error_occurred = True
                    err_event = RunErrorEvent(message=str(exc))
                    data = err_event.model_dump_json(by_alias=True)
                    yield f"event: {err_event.type.value}\ndata: {data}\n\n"
                finally:
                    if not error_occurred:
                        result = run_task.result() if run_task.done() else ""
                        fin_event = RunFinishedEvent(
                            thread_id=thread_id, run_id=run_id, result=result
                        )
                        data = fin_event.model_dump_json(by_alias=True)
                        yield f"event: {fin_event.type.value}\ndata: {data}\n\n"

                    # Clean up listener
                    if on_event in agent._event_listeners:
                        agent._event_listeners.remove(on_event)

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        @self.router.websocket("/ws")
        async def websocket_events(ws: WebSocket) -> None:
            await ws.accept()

            try:
                while True:
                    raw = await ws.receive_json()
                    request = RunRequest(**raw)
                    agent = self._get_agent(request.agent_name)
                    thread_id = request.thread_id or str(uuid.uuid4())
                    run_id = str(uuid.uuid4())

                    async def on_event(event: AgentEvent, data: dict[str, Any]) -> None:
                        translated = _translate_event(event, data, thread_id, run_id)
                        if translated:
                            await ws.send_text(translated.model_dump_json(by_alias=True))

                    agent.on_event(on_event)

                    try:
                        # Send RUN_STARTED
                        start = RunStartedEvent(
                            thread_id=thread_id,
                            run_id=run_id,
                            input={"message": request.message},
                        )
                        await ws.send_text(start.model_dump_json(by_alias=True))

                        result = await agent.chat(request.message)

                        fin = RunFinishedEvent(thread_id=thread_id, run_id=run_id, result=result)
                        await ws.send_text(fin.model_dump_json(by_alias=True))
                    except Exception as exc:
                        err = RunErrorEvent(message=str(exc))
                        await ws.send_text(err.model_dump_json(by_alias=True))
                    finally:
                        if on_event in agent._event_listeners:
                            agent._event_listeners.remove(on_event)

            except WebSocketDisconnect:
                pass
