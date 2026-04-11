# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Shared Pydantic models for agents, messages, and tools."""

from sagewai.models.agent import AgentConfig
from sagewai.models.inference import InferenceParams, InferencePreset
from sagewai.models.message import ChatMessage, Conversation, Role, ToolCall, UsageInfo
from sagewai.models.tool import ToolResult, ToolSpec, tool

__all__ = [
    "AgentConfig",
    "ChatMessage",
    "Conversation",
    "InferenceParams",
    "InferencePreset",
    "Role",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "UsageInfo",
    "tool",
]
