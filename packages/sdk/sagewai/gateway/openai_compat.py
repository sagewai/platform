# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""OpenAI-compatible /v1/chat/completions endpoint for sagewai agents."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage = ChatCompletionUsage()


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "sagewai"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


def create_openai_compat_router(agents: dict[str, Any]) -> APIRouter:
    """Create a FastAPI router implementing OpenAI-compatible endpoints.

    Args:
        agents: dict mapping agent name to BaseAgent instance.
    """
    router = APIRouter()

    @router.get("/v1/models")
    async def list_models() -> ModelListResponse:
        return ModelListResponse(
            data=[ModelInfo(id=name) for name in agents],
        )

    @router.post("/v1/chat/completions")
    async def chat_completions(req: ChatCompletionRequest) -> ChatCompletionResponse:
        agent = agents.get(req.model)
        if agent is None:
            raise HTTPException(404, f"Model '{req.model}' not found")

        # Extract last user message
        user_msg = ""
        for msg in reversed(req.messages):
            if msg.role == "user":
                user_msg = msg.content
                break

        response_text = await agent.chat(user_msg)

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=req.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=response_text),
                ),
            ],
        )

    return router
