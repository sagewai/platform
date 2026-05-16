# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""A2A server — FastAPI router serving agent card and JSON-RPC task endpoints.

Implements the A2A protocol server side:
- ``GET /.well-known/agent-card.json`` — agent discovery
- ``POST /a2a`` — JSON-RPC 2.0 for ``tasks/send``, ``tasks/get``, ``tasks/cancel``

Usage::

    from sagewai.protocols.a2a.server import A2AServer
    from sagewai.protocols.a2a.models import AgentCard

    a2a = A2AServer(
        card=AgentCard(name="my-agent"),
        handler=my_agent.chat,
    )
    app.include_router(a2a.router)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from sagewai.protocols.a2a.models import AgentCard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Task models (A2A protocol task lifecycle)
# ---------------------------------------------------------------------------


class TaskState(str, Enum):
    """A2A task lifecycle states."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"


class TaskStatus(BaseModel):
    """Current status of a task."""

    state: TaskState
    message: str = ""


class Artifact(BaseModel):
    """Output artifact produced by a task."""

    name: str = ""
    parts: list[dict[str, Any]] = Field(default_factory=list)


class Task(BaseModel):
    """A2A task — represents a unit of work delegated to an agent."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: TaskStatus = Field(default_factory=lambda: TaskStatus(state=TaskState.SUBMITTED))
    artifacts: list[Artifact] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# JSON-RPC models
# ---------------------------------------------------------------------------


class JsonRpcRequest(BaseModel):
    """JSON-RPC 2.0 request."""

    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class JsonRpcError(BaseModel):
    """JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: Any = None


# ---------------------------------------------------------------------------
# A2A Server
# ---------------------------------------------------------------------------

# Type alias for task handler: takes a message string, returns result string
TaskHandler = Callable[[str], Awaitable[str]]


class A2AServer:
    """FastAPI router implementing the A2A protocol server.

    Args:
        card: The AgentCard to serve at the well-known endpoint.
        handler: Async callable that processes task messages and returns results.
        api_key: Optional API key for simple authentication. If set,
            requests must include ``Authorization: Bearer <key>``.
        prefix: URL prefix for the JSON-RPC endpoint.
    """

    def __init__(
        self,
        card: AgentCard,
        handler: TaskHandler,
        *,
        api_key: str | None = None,
        prefix: str = "",
    ) -> None:
        self.card = card
        self.handler = handler
        self.api_key = api_key
        self._tasks: dict[str, Task] = {}
        self.router = APIRouter(prefix=prefix, tags=["a2a"])
        self._setup_routes()

    def _check_auth(self, authorization: str | None) -> None:
        """Validate API key if configured."""
        if not self.api_key:
            return
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        parts = authorization.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or parts[1] != self.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    def _setup_routes(self) -> None:
        @self.router.get("/.well-known/agent-card.json")
        async def agent_card_endpoint() -> JSONResponse:
            return JSONResponse(
                content=self.card.model_dump(by_alias=True, exclude_none=True),
                media_type="application/json",
            )

        @self.router.post("/a2a")
        async def jsonrpc_endpoint(
            request: Request,
            authorization: str | None = Header(default=None),
        ) -> JSONResponse:
            self._check_auth(authorization)

            body = await request.json()
            rpc = JsonRpcRequest(**body)

            method_map: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
                "tasks/send": self._handle_tasks_send,
                "tasks/get": self._handle_tasks_get,
                "tasks/cancel": self._handle_tasks_cancel,
            }

            handler = method_map.get(rpc.method)
            if not handler:
                return self._error_response(rpc.id, -32601, f"Method not found: {rpc.method}")

            try:
                result = await handler(rpc.params)
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": rpc.id,
                        "result": result,
                    }
                )
            except Exception as exc:
                logger.exception("A2A handler error for method %s", rpc.method)
                return self._error_response(rpc.id, -32000, str(exc))

    async def _handle_tasks_send(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/send — create and execute a task."""
        task_id = params.get("id", str(uuid.uuid4()))
        message = self._extract_message(params)

        task = Task(id=task_id, status=TaskStatus(state=TaskState.WORKING))
        self._tasks[task.id] = task

        try:
            result = await self.handler(message)
            task.status = TaskStatus(state=TaskState.COMPLETED)
            task.artifacts = [
                Artifact(
                    name="response",
                    parts=[{"type": "text", "text": result}],
                )
            ]
        except asyncio.CancelledError:
            task.status = TaskStatus(state=TaskState.CANCELED)
            raise
        except Exception as exc:
            task.status = TaskStatus(state=TaskState.FAILED, message=str(exc))
            raise

        return task.model_dump()

    async def _handle_tasks_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/get — retrieve task status."""
        task_id = params.get("id", "")
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")
        return task.model_dump()

    async def _handle_tasks_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/cancel — cancel a running task."""
        task_id = params.get("id", "")
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")
        task.status = TaskStatus(state=TaskState.CANCELED)
        return task.model_dump()

    @staticmethod
    def _extract_message(params: dict[str, Any]) -> str:
        """Extract the text message from task params."""
        message = params.get("message", "")
        if isinstance(message, dict):
            parts = message.get("parts", [])
            texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
            return " ".join(texts)
        return str(message)

    @staticmethod
    def _error_response(rpc_id: str | int | None, code: int, message: str) -> JSONResponse:
        """Build a JSON-RPC error response."""
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": code, "message": message},
            }
        )
