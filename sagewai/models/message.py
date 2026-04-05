# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Chat message types for agent conversations."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from sagewai.intelligence.multimodal.message import ContentPart


class Role(str, Enum):
    """Message role in a conversation."""

    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class ToolCall(BaseModel):
    """A tool invocation requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class UsageInfo(BaseModel):
    """Token usage metadata from an LLM response."""

    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    duration_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ChatMessage(BaseModel):
    """A single message in a conversation.

    Supports multimodal content via the optional ``parts`` field.
    When ``parts`` is set, the message may contain text, images, audio,
    or video.  The legacy ``content`` field is preserved for backward
    compatibility — text-only messages can continue to use it directly.
    """

    role: Role
    content: str | None = None
    parts: list[ContentPart] | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    usage: UsageInfo | None = None

    @property
    def text_content(self) -> str:
        """Get text content from either ``content`` field or text parts."""
        if self.content:
            return self.content
        if self.parts:
            return " ".join(
                p.text for p in self.parts if p.is_text and p.text
            )
        return ""

    @property
    def has_media(self) -> bool:
        """Check if message contains any media parts."""
        return bool(self.parts and any(p.is_media for p in self.parts))

    @classmethod
    def system(cls, content: str) -> ChatMessage:
        return cls(role=Role.system, content=content)

    @classmethod
    def user(cls, content: str) -> ChatMessage:
        return cls(role=Role.user, content=content)

    @classmethod
    def assistant(
        cls,
        content: str | None = None,
        tool_calls: list[ToolCall] | None = None,
        usage: UsageInfo | None = None,
    ) -> ChatMessage:
        return cls(role=Role.assistant, content=content, tool_calls=tool_calls, usage=usage)

    @classmethod
    def tool_result(cls, tool_call_id: str, name: str, content: str) -> ChatMessage:
        return cls(
            role=Role.tool,
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        )


class Conversation(BaseModel):
    """Ordered list of messages forming a conversation."""

    messages: list[ChatMessage] = Field(default_factory=list)

    def add_system(self, content: str) -> None:
        self.messages.append(ChatMessage.system(content))

    def add_user(self, content: str) -> None:
        self.messages.append(ChatMessage.user(content))

    def add_assistant(
        self,
        content: str | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> None:
        self.messages.append(ChatMessage.assistant(content, tool_calls))

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.messages.append(ChatMessage.tool_result(tool_call_id, name, content))

    def __len__(self) -> int:
        return len(self.messages)

    def __iter__(self):
        return iter(self.messages)
