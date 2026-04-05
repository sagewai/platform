# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Squashed migration — all tables (002-006 merged into 001).

Revision ID: 001_initial
Revises: None
Create Date: 2026-03-26 (squashed 2026-04-03)

Includes tables from:
- 001: core (users, orgs, workspaces, agents, workflows, prompts, sessions,
       budget, guardrails, eval, connectors, context, notifications, cloud)
- 002: saved_workflows, saved_workflow_versions, agent_runs.run_type
- 003: context_documents.tags (ARRAY + GIN index)
- 004: workers table, workflow_runs routing columns
- 005: fleet (enrollment_keys, fleet_audit_events, workers fleet columns)
- 006: harness (harness_keys, harness_policies, harness_spend, harness_audit)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── users ────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("oauth_provider", sa.String(50), nullable=True),
        sa.Column("oauth_id", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("totp_secret", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_oauth", "users", ["oauth_provider", "oauth_id"])

    # ── organizations ────────────────────────────────────────────────────
    op.create_table(
        "organizations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "owner_id",
            sa.Text(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)
    op.create_index("ix_organizations_owner", "organizations", ["owner_id"])

    # ── workspaces ───────────────────────────────────────────────────────
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "org_id",
            sa.Text(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("org_id", "slug"),
    )
    op.create_index("ix_workspaces_org", "workspaces", ["org_id"])

    # ── workspace_members ────────────────────────────────────────────────
    op.create_table(
        "workspace_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("workspace_id", "user_id"),
    )
    op.create_index("ix_wm_workspace", "workspace_members", ["workspace_id"])
    op.create_index("ix_wm_user", "workspace_members", ["user_id"])

    # ── llm_providers ────────────────────────────────────────────────────
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_name", sa.String(50), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("workspace_id", "provider_name"),
    )
    op.create_index("ix_llm_providers_workspace", "llm_providers", ["workspace_id"])

    # ── invitations ──────────────────────────────────────────────────────
    op.create_table(
        "invitations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column(
            "invited_by",
            sa.Text(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_invitations_email", "invitations", ["email"])
    op.create_index("ix_invitations_workspace", "invitations", ["workspace_id"])

    # ── projects (was tenants) ───────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("slug", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "environment",
            sa.Text(),
            nullable=False,
            server_default="development",
        ),
        sa.Column("allowed_origins", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("default_model", sa.Text(), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # ── agent_runs ───────────────────────────────────────────────────────
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="completed"),
        sa.Column("input_text", sa.Text(), server_default=""),
        sa.Column("output_text", sa.Text(), server_default=""),
        sa.Column("total_tokens", sa.Integer(), server_default="0"),
        sa.Column("input_tokens", sa.Integer(), server_default="0"),
        sa.Column("output_tokens", sa.Integer(), server_default="0"),
        sa.Column("cost_usd", sa.Double(), server_default="0.0"),
        sa.Column("model", sa.Text(), server_default=""),
        sa.Column("duration_ms", sa.Integer(), server_default="0"),
        sa.Column(
            "tool_calls",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.Double(), server_default="0.0"),
        sa.Column("completed_at", sa.Double(), server_default="0.0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("checkpoint_run_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        # from migration 002
        sa.Column("run_type", sa.Text(), nullable=False, server_default="standalone"),
        sa.Column("parent_workflow_run_id", sa.Text(), nullable=True),
    )
    op.create_index("idx_agent_runs_project_id", "agent_runs", ["project_id"])
    op.create_index(
        "idx_agent_runs_project_agent", "agent_runs", ["project_id", "agent_name"]
    )
    op.create_index("idx_agent_runs_agent_name", "agent_runs", ["agent_name"])
    op.create_index("idx_agent_runs_status", "agent_runs", ["status"])
    op.create_index("idx_agent_runs_started_at", "agent_runs", ["started_at"])
    op.create_index("idx_agent_runs_model", "agent_runs", ["model"])
    op.create_index("idx_agent_runs_run_type", "agent_runs", ["run_type"])
    op.create_index("idx_agent_runs_parent_wf", "agent_runs", ["parent_workflow_run_id"])

    # ── prompt_logs ──────────────────────────────────────────────────────
    op.create_table(
        "prompt_logs",
        sa.Column("log_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
            server_default="",
        ),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("step_index", sa.Integer(), server_default="0"),
        sa.Column("model", sa.Text(), server_default=""),
        sa.Column(
            "prompt_messages",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "response_message",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("input_tokens", sa.Integer(), server_default="0"),
        sa.Column("output_tokens", sa.Integer(), server_default="0"),
        sa.Column("cost_usd", sa.Double(), server_default="0.0"),
        sa.Column("duration_ms", sa.Integer(), server_default="0"),
        sa.Column("strategy", sa.Text(), server_default=sa.text("'react'")),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Interaction fields (from migration 017)
        sa.Column("is_example", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tags", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "source", sa.String(20), nullable=False, server_default="playground"
        ),
        sa.Column("input_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("output_text", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_prompt_logs_project_id", "prompt_logs", ["project_id"])
    op.create_index("idx_prompt_logs_run_id", "prompt_logs", ["run_id"])
    op.create_index("idx_prompt_logs_agent_name", "prompt_logs", ["agent_name"])
    op.create_index("idx_prompt_logs_model", "prompt_logs", ["model"])
    op.create_index("ix_prompt_logs_is_example", "prompt_logs", ["is_example"])
    op.create_index("ix_prompt_logs_source", "prompt_logs", ["source"])

    # ── workflow_runs ────────────────────────────────────────────────────
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("workflow_name", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column(
            "data",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("owner_id", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column(
            "input",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "steps_completed",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("steps_total", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        # from migration 004
        sa.Column("target_pool", sa.Text(), nullable=True),
        sa.Column("target_labels", postgresql.JSONB(), nullable=True),
        sa.Column("target_worker_id", sa.Text(), nullable=True),
        # from migration 005
        sa.Column("org_id", sa.Text(), nullable=True),
        sa.Column("target_model", sa.Text(), nullable=True),
        sa.Column("input_encrypted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("output_encrypted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("idx_workflow_runs_project_id", "workflow_runs", ["project_id"])
    op.create_index(
        "idx_workflow_runs_workflow_name", "workflow_runs", ["workflow_name"]
    )
    op.create_index("idx_workflow_runs_run_id", "workflow_runs", ["run_id"])
    op.create_index("idx_workflow_runs_status", "workflow_runs", ["status"])
    op.create_index(
        "idx_workflow_runs_name_status",
        "workflow_runs",
        ["workflow_name", "status"],
    )
    op.create_index("idx_workflow_runs_updated_at", "workflow_runs", ["updated_at"])
    op.create_index(
        "idx_workflow_runs_idempotency_key",
        "workflow_runs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "idx_wf_runs_target_pool",
        "workflow_runs",
        ["target_pool"],
        postgresql_where=sa.text("target_pool IS NOT NULL"),
    )

    # ── workflow_events ──────────────────────────────────────────────────
    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_workflow_events_project_id", "workflow_events", ["project_id"])
    op.create_index("idx_workflow_events_run_id", "workflow_events", ["run_id"])

    # ── sessions ─────────────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column(
            "messages",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("summary", sa.Text(), server_default=""),
        sa.Column(
            "memory_keys",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("session_id", "project_id"),
    )
    op.create_index("idx_sessions_project_id", "sessions", ["project_id"])
    op.create_index("idx_sessions_agent", "sessions", ["agent_name"])
    op.create_index("idx_sessions_updated", "sessions", ["updated_at"])

    # ── agent_access_tokens ──────────────────────────────────────────────
    op.create_table(
        "agent_access_tokens",
        sa.Column("token_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("token_suffix", sa.String(4), nullable=False, server_default=""),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("grantor_id", sa.Text(), nullable=False),
        sa.Column("scopes", sa.ARRAY(sa.Text()), server_default="{}"),
        sa.Column("status", sa.Text(), server_default="active"),
        sa.Column("single_use", sa.Boolean(), server_default="false"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_access_tokens_hash", "agent_access_tokens", ["token_hash"], unique=True
    )
    op.create_index("idx_access_tokens_project_id", "agent_access_tokens", ["project_id"])
    op.create_index("idx_access_tokens_agent", "agent_access_tokens", ["agent_name"])
    op.create_index("idx_access_tokens_status", "agent_access_tokens", ["status"])
    op.create_index("idx_access_tokens_expires", "agent_access_tokens", ["expires_at"])

    # ── cost_records ─────────────────────────────────────────────────────
    op.create_table(
        "cost_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("cost_usd", sa.Double(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_cost_records_project_id", "cost_records", ["project_id"])
    op.create_index("ix_cost_records_agent", "cost_records", ["agent_name"])
    op.create_index("ix_cost_records_model", "cost_records", ["model"])
    op.create_index("ix_cost_records_created", "cost_records", ["created_at"])

    # ── guardrail_events ─────────────────────────────────────────────────
    op.create_table(
        "guardrail_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("action", sa.String(50), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_guardrail_events_project_id", "guardrail_events", ["project_id"])
    op.create_index("ix_guardrail_events_agent", "guardrail_events", ["agent_name"])
    op.create_index("ix_guardrail_events_type", "guardrail_events", ["event_type"])
    op.create_index("ix_guardrail_events_created", "guardrail_events", ["created_at"])

    # ── budget_limits ────────────────────────────────────────────────────
    op.create_table(
        "budget_limits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("max_daily_usd", sa.Double(), nullable=False),
        sa.Column("max_monthly_usd", sa.Double(), nullable=False),
        sa.Column("action", sa.String(20), server_default="warn"),
        sa.Column("fallback_chain", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("project_id", "agent_name"),
    )
    op.create_index(
        "ix_budget_limits_project_agent",
        "budget_limits",
        ["project_id", "agent_name"],
        unique=True,
    )

    # ── budget_spend ─────────────────────────────────────────────────────
    op.create_table(
        "budget_spend",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("cost_usd", sa.Double(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_budget_spend_project_id", "budget_spend", ["project_id"])
    op.create_index("ix_budget_spend_agent", "budget_spend", ["agent_name"])
    op.create_index("ix_budget_spend_created", "budget_spend", ["created_at"])

    # ── guardrail_configs ────────────────────────────────────────────────
    op.create_table(
        "guardrail_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("guardrail_type", sa.String(50), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true"),
        sa.Column("config", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_guardrail_configs_project_agent_type",
        "guardrail_configs",
        ["project_id", "agent_name", "guardrail_type"],
        unique=True,
    )

    # ── eval_datasets ────────────────────────────────────────────────────
    op.create_table(
        "eval_datasets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cases", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("project_id", "name"),
    )
    op.create_index("ix_eval_datasets_project_id", "eval_datasets", ["project_id"])

    # ── eval_runs ────────────────────────────────────────────────────────
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column(
            "dataset_id",
            sa.Integer(),
            sa.ForeignKey("eval_datasets.id"),
            nullable=False,
        ),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("results", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_eval_runs_project_id", "eval_runs", ["project_id"])
    op.create_index("ix_eval_runs_dataset", "eval_runs", ["dataset_id"])

    # ── setup_state ──────────────────────────────────────────────────────
    op.create_table(
        "setup_state",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            server_default="1",
        ),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("org_name", sa.Text(), nullable=False),
        sa.Column("org_slug", sa.Text(), nullable=False),
        sa.Column("admin_email", sa.Text(), nullable=False),
        sa.Column("app_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact_email", sa.Text(), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(100), nullable=False, server_default="UTC"),
        sa.Column("industry", sa.Text(), nullable=False, server_default=""),
        sa.Column("team_size", sa.Text(), nullable=False, server_default=""),
        sa.Column("admin_password_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("admin_display_name", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "session_invalidated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id = 1", name="setup_state_single_row"),
    )

    # ── llm_provider_configs ─────────────────────────────────────────────
    op.create_table(
        "llm_provider_configs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column(
            "provider_type", sa.Text(), nullable=False, server_default="hosted"
        ),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="not_configured"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_llm_provider_configs_project_id", "llm_provider_configs", ["project_id"]
    )

    # ── playground_agents ────────────────────────────────────────────────
    op.create_table(
        "playground_agents",
        sa.Column("name", sa.String(255), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_playground_agents_project_id", "playground_agents", ["project_id"]
    )

    # ── connector_credentials ────────────────────────────────────────────
    op.create_table(
        "connector_credentials",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("connector_name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status", sa.String(50), nullable=False, server_default="not_configured"
        ),
        # OAuth2 columns
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_type", sa.String(50), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("project_id", "connector_name"),
    )
    op.create_index(
        "ix_connector_credentials_project_id",
        "connector_credentials",
        ["project_id"],
    )
    op.create_index(
        "ix_connector_credentials_project_connector",
        "connector_credentials",
        ["project_id", "connector_name"],
    )

    # ── connector_cursors ────────────────────────────────────────────────
    op.create_table(
        "connector_cursors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("connector_name", sa.String(100), nullable=False),
        sa.Column("channel", sa.String(255), nullable=False),
        sa.Column("cursor_value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("project_id", "connector_name", "channel"),
    )
    op.create_index(
        "ix_connector_cursors_project_id", "connector_cursors", ["project_id"]
    )

    # ── connector_triggers ───────────────────────────────────────────────
    op.create_table(
        "connector_triggers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "filter_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("target", sa.String(200), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column(
            "context_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_connector_triggers_project_id", "connector_triggers", ["project_id"]
    )

    # ── custom_connectors ────────────────────────────────────────────────
    op.create_table(
        "custom_connectors",
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column(
            "category",
            sa.String(100),
            nullable=False,
            server_default="custom",
        ),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "auth_type",
            sa.String(20),
            nullable=False,
            server_default="api_key",
        ),
        sa.Column(
            "auth_fields_json",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "mcp_command_json",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("docs_url", sa.Text(), nullable=True),
        sa.Column("agent_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("example_prompt", sa.Text(), nullable=False, server_default=""),
        # OAuth2 fields
        sa.Column("oauth_authorize_url", sa.Text(), nullable=True),
        sa.Column("oauth_token_url", sa.Text(), nullable=True),
        sa.Column(
            "oauth_scopes_json",
            sa.JSON(),
            nullable=True,
            server_default="[]",
        ),
        # Event support flags
        sa.Column(
            "supports_webhook",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "supports_listener",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "supports_poller",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_custom_connectors_project_id", "custom_connectors", ["project_id"]
    )

    # ── cloud_subscriptions ──────────────────────────────────────────────
    op.create_table(
        "cloud_subscriptions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_id", sa.String(50), nullable=False, server_default="free"),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_cloud_subscriptions_user", "cloud_subscriptions", ["user_id"]
    )

    # ── cloud_invoices ───────────────────────────────────────────────────
    op.create_table(
        "cloud_invoices",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="usd"),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("stripe_invoice_id", sa.String(255), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_cloud_invoices_user", "cloud_invoices", ["user_id"])

    # ── cloud_instances ──────────────────────────────────────────────────
    op.create_table(
        "cloud_instances",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("region", sa.String(50), nullable=False, server_default="us-central1"),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="provisioning",
        ),
        sa.Column("cloud_run_service_name", sa.String(255), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("plan_id", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_cloud_instances_user", "cloud_instances", ["user_id"])

    # ── cloud_notification_preferences ───────────────────────────────────
    op.create_table(
        "cloud_notification_preferences",
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "email_billing", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "email_security", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "email_product_updates",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── notification_channels ────────────────────────────────────────────
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("channel_type", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true"),
        sa.Column(
            "config",
            sa.Text(),
            server_default="{}",
            comment="JSONB for channel-specific settings",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("project_id", "channel_type"),
    )

    # ── notification_triggers ────────────────────────────────────────────
    op.create_table(
        "notification_triggers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("channel_type", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("project_id", "trigger", "channel_type"),
    )

    # ── notification_history ─────────────────────────────────────────────
    op.create_table(
        "notification_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("severity", sa.Text(), server_default="info"),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column("channel_type", sa.Text(), nullable=False),
        sa.Column("delivered", sa.Boolean(), server_default="false"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_notification_history_project_id",
        "notification_history",
        ["project_id"],
    )
    op.create_index(
        "ix_notification_history_trigger",
        "notification_history",
        ["trigger"],
    )
    op.create_index(
        "ix_notification_history_created_at",
        "notification_history",
        ["created_at"],
    )


    # ── context_documents ───────────────────────────────────────────────
    op.create_table(
        "context_documents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="upload"),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.Text(), nullable=False, server_default="text/plain"),
        sa.Column("file_size_bytes", sa.Integer(), server_default="0"),
        sa.Column("chunk_count", sa.Integer(), server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("confidence", sa.Float(), server_default="1.0"),
        sa.Column(
            "freshness_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        # from migration 003
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_index("idx_ctx_docs_project", "context_documents", ["project_id"])
    op.create_index("idx_ctx_docs_scope", "context_documents", ["scope", "scope_id"])
    op.create_index("idx_ctx_docs_status", "context_documents", ["status"])
    op.create_index("idx_ctx_docs_source", "context_documents", ["source"])
    op.create_index(
        "idx_ctx_docs_tags", "context_documents", ["tags"],
        postgresql_using="gin",
    )

    # ── context_chunks ────────────────────────────────────────────────
    op.create_table(
        "context_chunks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("context_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), server_default="0"),
        sa.Column("token_count", sa.Integer(), server_default="0"),
        sa.Column(
            "embedding_model",
            sa.Text(),
            server_default="text-embedding-3-small",
        ),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("importance", sa.Float(), server_default="0.5"),
        sa.Column("access_count", sa.Integer(), server_default="0"),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_ctx_chunks_doc", "context_chunks", ["document_id"])
    op.create_index("idx_ctx_chunks_project", "context_chunks", ["project_id"])
    op.create_index("idx_ctx_chunks_hash", "context_chunks", ["content_hash"])
    op.create_index("idx_ctx_chunks_scope", "context_chunks", ["scope", "scope_id"])
    op.create_index("idx_ctx_chunks_importance", "context_chunks", ["importance"])
    op.create_index("idx_ctx_chunks_accessed", "context_chunks", ["last_accessed_at"])

    # ── saved_workflows (from migration 002) ────────────────────────────
    op.create_table(
        "saved_workflows",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("yaml_content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "name"),
    )
    op.create_index("idx_saved_workflows_project", "saved_workflows", ["project_id"])
    op.create_index("idx_saved_workflows_name", "saved_workflows", ["name"])

    op.create_table(
        "saved_workflow_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workflow_id", sa.Text(), sa.ForeignKey("saved_workflows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("yaml_content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("workflow_id", "version"),
    )
    op.create_index("idx_saved_workflow_versions_wf", "saved_workflow_versions", ["workflow_id"])

    # ── workers (from migration 004+005) ────────────────────────────────
    op.create_table(
        "workers",
        sa.Column("worker_id", sa.Text(), primary_key=True),
        sa.Column("pool", sa.Text(), nullable=False, server_default="default"),
        sa.Column("labels", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("project_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("max_concurrent", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # from migration 005
        sa.Column("org_id", sa.Text(), nullable=True),
        sa.Column("models_supported", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("models_canonical", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("approval_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("probe_status", sa.Text(), nullable=True),
        sa.Column("sdk_version", sa.Text(), nullable=True),
    )
    op.create_index("idx_workers_pool", "workers", ["pool"])
    op.create_index("idx_workers_status", "workers", ["status"])
    op.create_index("idx_workers_project_id", "workers", ["project_id"])
    op.create_index(
        "idx_workers_heartbeat", "workers", ["last_heartbeat"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_workers_org_approval", "workers", ["org_id", "approval_status"])
    op.create_index(
        "ix_workers_models_canonical", "workers", ["models_canonical"],
        postgresql_using="gin",
    )

    # ── enrollment_keys (from migration 005) ────────────────────────────
    op.create_table(
        "enrollment_keys",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("current_uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allowed_pools", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("allowed_models", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_enrollment_keys_org", "enrollment_keys", ["org_id"])

    # ── fleet_audit_events (from migration 005) ─────────────────────────
    op.create_table(
        "fleet_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fleet_audit_org_created", "fleet_audit_events", ["org_id", "created_at"])

    # ── harness_keys (from migration 006) ───────────────────────────────
    op.create_table(
        "harness_keys",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("key_suffix", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("team_id", sa.Text(), nullable=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("allowed_models", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("max_budget_daily_usd", sa.Float(), nullable=True),
        sa.Column("max_budget_monthly_usd", sa.Float(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_harness_keys_org_id", "harness_keys", ["org_id"])
    op.create_index("ix_harness_keys_user_id", "harness_keys", ["user_id"])

    # ── harness_policies (from migration 006) ───────────────────────────
    op.create_table(
        "harness_policies",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("org_id", sa.Text(), nullable=True),
        sa.Column("team_id", sa.Text(), nullable=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier_overrides", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("blocked_models", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("allowed_models", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("max_tier", sa.Text(), nullable=True),
        sa.Column("force_model", sa.Text(), nullable=True),
        sa.Column("allow_override", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_harness_policies_scope", "harness_policies", ["org_id", "team_id", "project_id", "user_id"])

    # ── harness_spend (from migration 006) ──────────────────────────────
    op.create_table(
        "harness_spend",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("team_id", sa.Text(), nullable=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("org_id", sa.Text(), server_default="default"),
        sa.Column("model_requested", sa.Text(), nullable=True),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column("complexity_tier", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0"),
        sa.Column("output_tokens", sa.Integer(), server_default="0"),
        sa.Column("cost_usd", sa.Float(), server_default="0.0"),
        sa.Column("latency_ms", sa.Float(), server_default="0.0"),
        sa.Column("policy_applied", sa.Text(), nullable=True),
        sa.Column("budget_action", sa.Text(), nullable=True),
        sa.Column("key_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("harness_keys.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_harness_spend_user_id", "harness_spend", ["user_id"])
    op.create_index("ix_harness_spend_org_id", "harness_spend", ["org_id"])
    op.create_index("ix_harness_spend_timestamp", "harness_spend", ["timestamp"])
    op.create_index("ix_harness_spend_model_used", "harness_spend", ["model_used"])

    # ── harness_audit (from migration 006) ──────────────────────────────
    op.create_table(
        "harness_audit",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("org_id", sa.Text(), server_default="default"),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_harness_audit_timestamp", "harness_audit", ["timestamp"])
    op.create_index("ix_harness_audit_event_type", "harness_audit", ["event_type"])


def downgrade() -> None:
    # Harness (006)
    op.drop_table("harness_audit")
    op.drop_table("harness_spend")
    op.drop_table("harness_policies")
    op.drop_table("harness_keys")
    # Fleet (005)
    op.drop_table("fleet_audit_events")
    op.drop_table("enrollment_keys")
    # Workers (004+005)
    op.drop_table("workers")
    # Workflow registry (002)
    op.drop_table("saved_workflow_versions")
    op.drop_table("saved_workflows")
    # Context (001)
    op.drop_table("context_chunks")
    op.drop_table("context_documents")
    op.drop_table("notification_history")
    op.drop_table("notification_triggers")
    op.drop_table("notification_channels")
    op.drop_table("cloud_notification_preferences")
    op.drop_table("cloud_instances")
    op.drop_table("cloud_invoices")
    op.drop_table("cloud_subscriptions")
    op.drop_table("custom_connectors")
    op.drop_table("connector_triggers")
    op.drop_table("connector_cursors")
    op.drop_table("connector_credentials")
    op.drop_table("playground_agents")
    op.drop_table("llm_provider_configs")
    op.drop_table("setup_state")
    op.drop_table("eval_runs")
    op.drop_table("eval_datasets")
    op.drop_table("guardrail_configs")
    op.drop_table("budget_spend")
    op.drop_table("budget_limits")
    op.drop_table("guardrail_events")
    op.drop_table("cost_records")
    op.drop_table("agent_access_tokens")
    op.drop_table("sessions")
    op.drop_table("workflow_events")
    op.drop_table("workflow_runs")
    op.drop_table("prompt_logs")
    op.drop_table("agent_runs")
    op.drop_table("projects")
    op.drop_table("invitations")
    op.drop_table("llm_providers")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("organizations")
    op.drop_table("users")
