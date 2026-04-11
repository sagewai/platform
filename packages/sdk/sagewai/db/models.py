# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""SQLAlchemy declarative models for the sagecurator database.

These models are the single source of truth for the database schema.
Alembic diffs them against the live DB to auto-generate migrations.

The existing stores (RunStore, PromptStore) continue using raw asyncpg
queries. These models exist for schema definition only.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


class AgentRun(Base):
    """An agent run record — mirrors admin.store.RunRecord."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("idx_agent_runs_agent_name", "agent_name"),
        Index("idx_agent_runs_status", "status"),
        Index("idx_agent_runs_started_at", "started_at"),
        Index("idx_agent_runs_model", "model"),
        Index("idx_agent_runs_run_type", "run_type"),
        Index("idx_agent_runs_parent_wf", "parent_workflow_run_id"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="completed")
    input_text: Mapped[str] = mapped_column(Text, server_default="")
    output_text: Mapped[str] = mapped_column(Text, server_default="")
    total_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Double, server_default="0.0")
    model: Mapped[str] = mapped_column(Text, server_default="")
    duration_ms: Mapped[int] = mapped_column(Integer, server_default="0")
    tool_calls: Mapped[dict | None] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[float] = mapped_column(Double, server_default="0.0")
    completed_at: Mapped[float] = mapped_column(Double, server_default="0.0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="standalone")
    parent_workflow_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowRunModel(Base):
    """A durable workflow run checkpoint."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("idx_workflow_runs_workflow_name", "workflow_name"),
        Index("idx_workflow_runs_run_id", "run_id"),
        Index("idx_workflow_runs_status", "status"),
        Index("idx_workflow_runs_name_status", "workflow_name", "status"),
        Index("idx_workflow_runs_updated_at", "updated_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workflow_name: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    owner_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromptLog(Base):
    """A per-step prompt log entry — mirrors observability.prompt_store.PromptLogRecord."""

    __tablename__ = "prompt_logs"
    __table_args__ = (
        Index("idx_prompt_logs_run_id", "run_id"),
        Index("idx_prompt_logs_agent_name", "agent_name"),
        Index("idx_prompt_logs_model", "model"),
    )

    log_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        server_default="",
    )
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, server_default="0")
    model: Mapped[str] = mapped_column(Text, server_default="")
    prompt_messages: Mapped[dict | None] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    response_message: Mapped[dict | None] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Double, server_default="0.0")
    duration_ms: Mapped[int] = mapped_column(Integer, server_default="0")
    strategy: Mapped[str] = mapped_column(Text, server_default=text("'react'"))
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Admin store tables (migration 005)
# ---------------------------------------------------------------------------


class CostRecord(Base):
    """Per-call cost tracking for analytics."""

    __tablename__ = "cost_records"
    __table_args__ = (
        Index("ix_cost_records_agent", "agent_name"),
        Index("ix_cost_records_model", "model"),
        Index("ix_cost_records_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    cost_usd: Mapped[float] = mapped_column(Double)
    tokens: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GuardrailEventModel(Base):
    """Logged guardrail event (PII detection, hallucination flag, etc.)."""

    __tablename__ = "guardrail_events"
    __table_args__ = (
        Index("ix_guardrail_events_agent", "agent_name"),
        Index("ix_guardrail_events_type", "event_type"),
        Index("ix_guardrail_events_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetLimitModel(Base):
    """Per-agent budget limit configuration."""

    __tablename__ = "budget_limits"
    __table_args__ = (Index("ix_budget_limits_agent", "agent_name", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(255), unique=True)
    max_daily_usd: Mapped[float] = mapped_column(Double)
    max_monthly_usd: Mapped[float] = mapped_column(Double)
    action: Mapped[str] = mapped_column(String(20), server_default="warn")
    fallback_chain: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BudgetSpend(Base):
    """Individual spend event for budget tracking."""

    __tablename__ = "budget_spend"
    __table_args__ = (
        Index("ix_budget_spend_agent", "agent_name"),
        Index("ix_budget_spend_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(255))
    cost_usd: Mapped[float] = mapped_column(Double)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GuardrailConfig(Base):
    """Per-agent guardrail configuration."""

    __tablename__ = "guardrail_configs"
    __table_args__ = (
        Index(
            "ix_guardrail_configs_agent_type",
            "agent_name",
            "guardrail_type",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(255))
    guardrail_type: Mapped[str] = mapped_column(String(50))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    config: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EvalDatasetModel(Base):
    """Stored evaluation dataset."""

    __tablename__ = "eval_datasets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cases: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalRunModel(Base):
    """Stored evaluation run result."""

    __tablename__ = "eval_runs"
    __table_args__ = (Index("ix_eval_runs_dataset", "dataset_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(Integer, ForeignKey("eval_datasets.id"))
    agent_name: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    results: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Cloud tables (migration 006)
# ---------------------------------------------------------------------------


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email", "email", unique=True),
        Index("ix_users_oauth", "oauth_provider", "oauth_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # UUID
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    oauth_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrganizationModel(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        Index("ix_organizations_slug", "slug", unique=True),
        Index("ix_organizations_owner", "owner_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # UUID
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    owner_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkspaceModel(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        Index("ix_workspaces_org_slug", "org_id", "slug", unique=True),
        Index("ix_workspaces_org", "org_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # UUID
    org_id: Mapped[str] = mapped_column(Text, ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(100))
    settings: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSONB
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Workflow registry tables (migration 002)
# ---------------------------------------------------------------------------


class SavedWorkflowModel(Base):
    """A saved workflow definition in the registry."""

    __tablename__ = "saved_workflows"
    __table_args__ = (
        Index("idx_saved_workflows_project", "project_id"),
        Index("idx_saved_workflows_name", "project_id", "name"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SavedWorkflowVersionModel(Base):
    """Version history for a saved workflow."""

    __tablename__ = "saved_workflow_versions"
    __table_args__ = (Index("idx_saved_workflow_versions_wf", "workflow_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        Text, ForeignKey("saved_workflows.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkspaceMemberModel(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        Index("ix_wm_workspace_user", "workspace_id", "user_id", unique=True),
        Index("ix_wm_workspace", "workspace_id"),
        Index("ix_wm_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(Text, ForeignKey("workspaces.id"))
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(20))  # owner, admin, member, viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMProviderModel(Base):
    __tablename__ = "llm_providers"
    __table_args__ = (
        Index("ix_llm_providers_workspace", "workspace_id"),
        Index("ix_llm_providers_ws_provider", "workspace_id", "provider_name", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(Text, ForeignKey("workspaces.id"))
    provider_name: Mapped[str] = mapped_column(String(50))
    api_key_encrypted: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvitationModel(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        Index("ix_invitations_email", "email"),
        Index("ix_invitations_workspace", "workspace_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # UUID
    workspace_id: Mapped[str] = mapped_column(Text, ForeignKey("workspaces.id"))
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    invited_by: Mapped[str] = mapped_column(Text, ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
