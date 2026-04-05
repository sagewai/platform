# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Pydantic response models for the admin API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentSummary(BaseModel):
    """Summary of a registered agent."""

    name: str
    capabilities: list[str] = Field(default_factory=list)
    model: str = ""
    status: str = "idle"
    source: str = "registered"  # "registered" | "playground"
    strategy: str = ""
    tags: list[str] = Field(default_factory=list)


class AgentDetail(BaseModel):
    """Detailed info about a registered agent."""

    name: str
    capabilities: list[str] = Field(default_factory=list)
    model: str = ""
    system_prompt: str = ""
    max_iterations: int = 10
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    memory_backends: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    status: str = "idle"
    source: str = "registered"
    strategy: str = ""
    tags: list[str] = Field(default_factory=list)
    fallback_models: list[str] = Field(default_factory=list)
    total_runs: int = 0
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    preset: str | None = None


class RunSummary(BaseModel):
    """Summary of an agent run."""

    run_id: str
    agent_name: str
    status: str = "completed"
    input_preview: str = ""
    output_preview: str = ""
    started_at: float | None = None
    completed_at: float | None = None
    total_tokens: int = 0
    run_type: str = "standalone"
    parent_workflow_run_id: str | None = None


class RunDetail(BaseModel):
    """Detailed info about a single run."""

    run_id: str
    agent_name: str
    status: str = "completed"
    input_text: str = ""
    output_text: str = ""
    started_at: float | None = None
    completed_at: float | None = None
    total_tokens: int = 0
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    steps: list[StepInfo] = Field(default_factory=list)


class ToolCallRecord(BaseModel):
    """Record of a tool call within a run."""

    tool_name: str
    arguments: str = ""
    result_preview: str = ""
    duration_ms: int = 0


class StepInfo(BaseModel):
    """A step within a run (e.g., LLM call, tool call)."""

    step_type: str  # "llm_call", "tool_call", "memory_query"
    detail: str = ""
    duration_ms: int = 0


class SessionInfo(BaseModel):
    """Info about an active session."""

    session_id: str
    agent_name: str
    started_at: float
    message_count: int = 0
    status: str = "active"


class ConfigUpdateRequest(BaseModel):
    """Request body for updating agent configuration at runtime."""

    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    top_p: float | None = Field(None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(None, ge=1)
    frequency_penalty: float | None = Field(None, ge=-2.0, le=2.0)
    presence_penalty: float | None = Field(None, ge=-2.0, le=2.0)
    max_iterations: int | None = Field(None, ge=1, le=100)
    strategy: str | None = None
    tags: list[str] | None = None
    fallback_models: list[str] | None = None
    context_scopes: list[str] | None = None
    retrieval_config: dict[str, Any] | None = None
    directive_template: str | None = None
    auto_learn: bool | None = None


class ControlActionResponse(BaseModel):
    """Response for run control actions (pause/resume/cancel)."""

    run_id: str
    action: str
    status: str


class HealthSnapshot(BaseModel):
    """Snapshot of an agent's current health state."""

    agent_name: str
    state: str
    error_rate: float = 0.0
    latency_p95: float = 0.0
    window_size: int = 0
    recent_successes: int = 0
    recent_failures: int = 0
    consecutive_successes: int = 0


# Fix forward reference for RunDetail
RunDetail.model_rebuild()
