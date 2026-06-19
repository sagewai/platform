# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SQLAlchemy declarative models for the sagecurator database.

These models are the single source of truth for the schema that
``Base.metadata.create_all()`` produces on SQLite (used by the SQLite
Core stores). On PostgreSQL the live schema is managed by Alembic
migrations; ``create_all`` is not used there.

JSON portability
----------------
All JSON/JSONB columns use ``JSONType``:

    JSONType = JSON().with_variant(JSONB(), "postgresql")

On SQLite this renders as the built-in ``JSON`` type (TEXT + Python
serialisation). On PostgreSQL it uses the native ``JSONB`` type.
``::jsonb`` server defaults are intentionally omitted — the Python
``default=`` argument covers all ORM inserts, and the Core stores
always supply explicit values.

Columns that Postgres stores as ``ARRAY(Text())`` (e.g.
``effective_env_keys``, ``effective_secret_keys``) use ``ArrayText``:

    ArrayText = JSON().with_variant(ARRAY(Text()), "postgresql")

On SQLite this round-trips as a JSON list. On PostgreSQL it binds to
the native ``TEXT[]`` type that migration 003 creates, avoiding the
JSONB-vs-TEXT[] mismatch that SQLAlchemy Core would otherwise produce.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---------------------------------------------------------------------------
# Portable JSON type — JSON on SQLite, JSONB on PostgreSQL
# ---------------------------------------------------------------------------

JSONType = JSON().with_variant(JSONB(), "postgresql")

# Portable UUID — TEXT on SQLite, native UUID on PostgreSQL (matches migration 001).
UuidText = Text().with_variant(PG_UUID(as_uuid=False), "postgresql")

# Postgres TEXT[] columns; JSON list on SQLite. Round-trips as list[str] on both.
ArrayText = JSON().with_variant(ARRAY(Text()), "postgresql")

# Portable BigInteger PK — SQLite requires INTEGER for rowid autoincrement;
# on PostgreSQL this renders as BIGINT (matching migration BIGSERIAL columns).
BigIntPK = Integer().with_variant(BigInteger(), "postgresql")


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


# ---------------------------------------------------------------------------
# Core stores tables — workflow_runs, sessions, sealed_revocations
# These three must be kept in sync with migrations 001–008.
# ---------------------------------------------------------------------------


class WorkflowRunModel(Base):
    """A durable workflow run checkpoint.

    Columns are the complete union of migrations 001–008 as applied to
    the ``workflow_runs`` table. See each migration for the authoritative
    type/nullability.
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("idx_workflow_runs_workflow_name", "workflow_name"),
        Index("idx_workflow_runs_run_id", "run_id"),
        Index("idx_workflow_runs_status", "status"),
        Index("idx_workflow_runs_name_status", "workflow_name", "status"),
        Index("idx_workflow_runs_updated_at", "updated_at"),
        Index("idx_workflow_runs_project_id", "project_id"),
        Index("idx_workflow_runs_replay_of", "replay_of_run_id"),
        Index(
            "idx_workflow_runs_idempotency_key",
            "idempotency_key",
            unique=True,
            sqlite_where=text("idempotency_key IS NOT NULL"),
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    # ── 001_initial ──────────────────────────────────────────────────────
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    workflow_name: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    # data — JSONB on pg, JSON (TEXT) on SQLite; stores the full WorkflowRun dict
    data: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    owner_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # input / output — JSONB envelope for queue metadata
    input: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    output: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_completed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    steps_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # fleet routing columns (from 001 migration 004 section)
    target_pool: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_labels: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    target_worker_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # harness / fleet columns (from 001 migration 005 section)
    org_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    output_encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # ── 002_sandbox_requirements ─────────────────────────────────────────
    requires_sandbox_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="none"
    )
    requires_image: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="ghcr.io/sagewai/sandbox-base:0.0.0-dev",
    )
    requires_variant: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_network_policy: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="none"
    )

    # ── 003_sealed ───────────────────────────────────────────────────────
    security_profile_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    # On Postgres these are ARRAY(Text()); on SQLite we store them as JSON lists.
    effective_env_keys: Mapped[list] = mapped_column(ArrayText, nullable=False, default=list)
    effective_secret_keys: Mapped[list] = mapped_column(ArrayText, nullable=False, default=list)

    # ── 004_sealed_revocations ────────────────────────────────────────────
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── 005_execution_mode ───────────────────────────────────────────────
    execution_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="bare")

    # ── 006_artifact_destination ─────────────────────────────────────────
    artifact_destination: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    # ── 006_replay_snapshots ─────────────────────────────────────────────
    replay_of_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    replay_from_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── 008_directives ───────────────────────────────────────────────────
    directive_chain: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    replay_re_evaluate_directives: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    execution_mode_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_from: Mapped[str | None] = mapped_column(Text, nullable=True)


class SessionModel(Base):
    """A stored agent session — mirrors sessions table from migration 001.

    Composite primary key ``(session_id, project_id)`` matches the
    ``PrimaryKeyConstraint`` in the initial migration.

    ``created_at`` / ``updated_at`` are timezone-aware DateTime columns;
    the Postgres session store writes them via ``to_timestamp($N)`` which
    produces a timestamptz value.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "project_id"),
        Index("idx_sessions_project_id", "project_id"),
        Index("idx_sessions_agent", "agent_name"),
        Index("idx_sessions_updated", "updated_at"),
    )

    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    messages: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_keys: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SealedRevocationModel(Base):
    """A sealed secret revocation record — from migration 004.

    The unique partial index on ``(profile_id, secret_key) WHERE lifted_at IS NULL``
    enforces "at most one active revocation per (profile_id, secret_key)".
    SQLAlchemy renders the ``sqlite_where`` / ``postgresql_where`` clause on
    both dialects, so ``create_all`` on SQLite produces a real partial-unique
    index matching migration 004's ``idx_sealed_revocations_active``.
    """

    __tablename__ = "sealed_revocations"
    __table_args__ = (
        Index("idx_sealed_revocations_profile", "profile_id"),
        Index(
            "idx_sealed_revocations_active",
            "profile_id",
            "secret_key",
            unique=True,
            sqlite_where=text("lifted_at IS NULL"),
            postgresql_where=text("lifted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(Text, nullable=False)
    secret_key: Mapped[str] = mapped_column(Text, nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    hard: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifted_by: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Agent run / prompt log tables
# ---------------------------------------------------------------------------


class AgentRun(Base):
    """An agent run record — mirrors admin.store.RunRecord."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("idx_agent_runs_project_id", "project_id"),
        Index("idx_agent_runs_project_agent", "project_id", "agent_name"),
        Index("idx_agent_runs_agent_name", "agent_name"),
        Index("idx_agent_runs_status", "status"),
        Index("idx_agent_runs_started_at", "started_at"),
        Index("idx_agent_runs_model", "model"),
        Index("idx_agent_runs_run_type", "run_type"),
        Index("idx_agent_runs_parent_wf", "parent_workflow_run_id"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True, server_default="default")
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
    tool_calls: Mapped[list | None] = mapped_column(JSONType, default=list)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONType, default=dict)
    started_at: Mapped[float] = mapped_column(Double, server_default="0.0")
    completed_at: Mapped[float] = mapped_column(Double, server_default="0.0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="standalone")
    parent_workflow_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromptLog(Base):
    """A per-step prompt log entry — mirrors observability.prompt_store.PromptLogRecord."""

    __tablename__ = "prompt_logs"
    __table_args__ = (
        Index("idx_prompt_logs_project_id", "project_id"),
        Index("idx_prompt_logs_run_id", "run_id"),
        Index("idx_prompt_logs_agent_name", "agent_name"),
        Index("idx_prompt_logs_model", "model"),
    )

    log_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True, server_default="default")
    run_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        server_default="",
    )
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, server_default="0")
    model: Mapped[str] = mapped_column(Text, server_default="")
    prompt_messages: Mapped[list | None] = mapped_column(JSONType, default=list)
    response_message: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Double, server_default="0.0")
    duration_ms: Mapped[int] = mapped_column(Integer, server_default="0")
    strategy: Mapped[str] = mapped_column(Text, server_default="react")
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONType, default=dict)
    # Interaction fields (from migration 017)
    is_example: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    tags: Mapped[str] = mapped_column(Text, nullable=False, server_default="[]")
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="playground")
    input_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    output_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Playground tables
# ---------------------------------------------------------------------------


class PlaygroundAgentModel(Base):
    """Playground agent spec — mirrors playground_agents table from migration 001.

    ``name`` is the PRIMARY KEY (globally unique, String(255) per migration).
    ``spec`` is stored as TEXT (the migration uses ``sa.Text()``, not JSONB);
    the store serialises/deserialises via ``json.dumps``/``json.loads``.
    ``created_at`` is append-only; ``updated_at`` is set on every upsert.
    Upsert targets the PK with ``index_elements=["name"]``.
    """

    __tablename__ = "playground_agents"
    __table_args__ = (Index("ix_playground_agents_project_id", "project_id"),)

    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    spec: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProviderModel(Base):
    """Tenant-scoped LLM provider config (multi-tenant mode).

    ``project_id`` is the single scope tag: NULL = global/org-shared, a value =
    isolated to that project. Secret fields inside ``data`` are encrypted under
    the per-project data key (tenant_keys). One default provider per scope.
    """

    __tablename__ = "provider"
    __table_args__ = (
        Index("ux_provider_global_name", "provider_name", unique=True,
              sqlite_where=text("project_id IS NULL"),
              postgresql_where=text("project_id IS NULL")),
        Index("ux_provider_proj_name", "project_id", "provider_name", unique=True,
              sqlite_where=text("project_id IS NOT NULL"),
              postgresql_where=text("project_id IS NOT NULL")),
        Index("ix_provider_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    data: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantAgentModel(Base):
    """Tenant-scoped playground agent (multi-tenant mode).

    ``project_id`` is the single scope tag (NULL = global; value = that project).
    ``spec`` is the full agent spec JSON. One agent name per scope.
    """

    __tablename__ = "agent"
    __table_args__ = (
        Index("ux_agent_global_name", "name", unique=True,
              sqlite_where=text("project_id IS NULL"),
              postgresql_where=text("project_id IS NULL")),
        Index("ux_agent_proj_name", "project_id", "name", unique=True,
              sqlite_where=text("project_id IS NOT NULL"),
              postgresql_where=text("project_id IS NOT NULL")),
        Index("ix_agent_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectionModel(Base):
    """Tenant-scoped connection config (multi-tenant mode).

    ``project_id`` is the single scope tag: NULL = org-shared, a value = isolated
    to that project. Protocol credentials remain inside ``protocol_data`` and are
    encrypted by the connection credentials router before persistence.
    """

    __tablename__ = "connection"
    __table_args__ = (
        Index(
            "ux_connection_global_name",
            "protocol",
            "display_name",
            unique=True,
            sqlite_where=text("project_id IS NULL"),
            postgresql_where=text("project_id IS NULL"),
        ),
        Index(
            "ux_connection_proj_name",
            "project_id",
            "protocol",
            "display_name",
            unique=True,
            sqlite_where=text("project_id IS NOT NULL"),
            postgresql_where=text("project_id IS NOT NULL"),
        ),
        Index("ix_connection_project_id", "project_id"),
        Index("ix_connection_protocol", "protocol"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    protocol: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    credentials_backend: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    last_tested_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    last_error: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    protocol_data: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminResourceModel(Base):
    """Generic project-scoped admin control-plane resource (multi-tenant mode).

    One table backs every currently-file-backed admin resource — budgets,
    guardrails, saved workflows, eval datasets, notification channels/triggers,
    connector triggers, artifact destinations — keyed by ``kind``. ``data`` is an
    opaque JSON blob the store never interprets (secret encryption is the route's
    job). ``project_id`` is the single scope tag: NULL = org-shared, a value =
    isolated to that project.

    The composite PK ``(kind, resource_id)`` makes a resource id stable across
    kinds. The NULL-safe partial-unique index on ``(kind, project_id, name)``
    WHERE ``name IS NOT NULL`` gives one name per (kind, project) and one global
    name per kind, while letting unnamed rows coexist freely.
    """

    __tablename__ = "admin_resources"
    __table_args__ = (
        PrimaryKeyConstraint("kind", "resource_id"),
        Index("ux_admin_resources_kind_proj_name", "kind", "project_id", "name", unique=True,
              sqlite_where=text("name IS NOT NULL"),
              postgresql_where=text("name IS NOT NULL")),
        Index("ix_admin_resources_project_id", "project_id"),
        Index("ix_admin_resources_kind", "kind"),
    )

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateLimitModel(Base):
    """Distributed fixed-window rate-limit counter (multi-tenant mode).

    Backs :class:`sagewai.db.rate_limit.PostgresRateLimiter`. Each row is one
    ``(bucket_key, window_start)`` window; ``count`` is incremented atomically via
    ``INSERT ... ON CONFLICT DO UPDATE``. Because every worker process targets the
    same row, the limit is enforced across processes (the single-process
    in-memory limiter is the single-org default and needs no table). Stale
    windows are pruned opportunistically on write.
    """

    __tablename__ = "rate_limits"
    __table_args__ = (
        PrimaryKeyConstraint("bucket_key", "window_start"),
        Index("ix_rate_limits_window_start", "window_start"),
    )

    bucket_key: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class ApiTokenModel(Base):
    """Tenant-scoped API token (machine/CI auth) for multi-tenant mode.

    A bearer token used for non-interactive (CI/automation) access. It carries
    ``read/write/admin`` ``scopes`` AND is bound to a scope via ``project_id``:
    a value = the token acts only in that project; NULL = an ORG-SHARED token
    (org-shared resources only — **NOT** an all-projects wildcard). The effective
    permission at request time is the INTERSECTION of these scopes and the
    subject's resolved role, so a token never exceeds its owner's role.

    Only the SHA-256 ``token_hash`` is stored (the plaintext is returned once at
    creation). ``token_hash`` is globally unique and indexed for the pre-context
    auth lookup. Name uniqueness is NULL-safe per the two partial-unique indexes
    (one org-shared name, one name per project), matching the resource tables.
    """

    __tablename__ = "api_token"
    __table_args__ = (
        Index("ux_api_token_hash", "token_hash", unique=True),
        Index("ix_api_token_org_project", "org_id", "project_id"),
        Index("ux_api_token_org_name", "org_id", "name", unique=True,
              sqlite_where=text("project_id IS NULL AND name IS NOT NULL"),
              postgresql_where=text("project_id IS NULL AND name IS NOT NULL")),
        Index("ux_api_token_org_proj_name", "org_id", "project_id", "name", unique=True,
              sqlite_where=text("project_id IS NOT NULL AND name IS NOT NULL"),
              postgresql_where=text("project_id IS NOT NULL AND name IS NOT NULL")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Admin store tables
# ---------------------------------------------------------------------------


class CostRecord(Base):
    """Per-call cost tracking for analytics."""

    __tablename__ = "cost_records"
    __table_args__ = (
        Index("ix_cost_records_project_id", "project_id"),
        Index("ix_cost_records_agent", "agent_name"),
        Index("ix_cost_records_model", "model"),
        Index("ix_cost_records_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    agent_name: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    cost_usd: Mapped[float] = mapped_column(Double)
    tokens: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GuardrailEventModel(Base):
    """Logged guardrail event (PII detection, hallucination flag, etc.)."""

    __tablename__ = "guardrail_events"
    __table_args__ = (
        Index("ix_guardrail_events_project_id", "project_id"),
        Index("ix_guardrail_events_agent", "agent_name"),
        Index("ix_guardrail_events_type", "event_type"),
        Index("ix_guardrail_events_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    agent_name: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetLimitModel(Base):
    """Per-agent budget limit configuration."""

    __tablename__ = "budget_limits"
    __table_args__ = (
        Index("ix_budget_limits_project_agent", "project_id", "agent_name", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    agent_name: Mapped[str] = mapped_column(String(255))
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
        Index("ix_budget_spend_project_id", "project_id"),
        Index("ix_budget_spend_agent", "agent_name"),
        Index("ix_budget_spend_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    agent_name: Mapped[str] = mapped_column(String(255))
    cost_usd: Mapped[float] = mapped_column(Double)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GuardrailConfig(Base):
    """Per-agent guardrail configuration."""

    __tablename__ = "guardrail_configs"
    __table_args__ = (
        Index(
            "ix_guardrail_configs_project_agent_type",
            "project_id",
            "agent_name",
            "guardrail_type",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
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
# Cloud tables
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
    settings: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSONB on pg
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Workflow registry tables
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


# ---------------------------------------------------------------------------
# Gateway tables
# ---------------------------------------------------------------------------


class AgentAccessTokenModel(Base):
    """Access token record — mirrors agent_access_tokens table from migration 001.

    PK is ``token_id`` (Text); unique constraint on ``token_hash``.
    Timestamps are stored as timezone-aware DateTime (the old asyncpg store
    passed Unix floats via ``to_timestamp($N)`` which produced timestamptz).
    ``scopes`` maps to ``ARRAY(Text())`` on PostgreSQL and JSON list on SQLite.
    Upsert uses ``ON CONFLICT (token_id)`` (the PK).
    """

    __tablename__ = "agent_access_tokens"
    __table_args__ = (
        Index("idx_access_tokens_hash", "token_hash", unique=True),
        Index("idx_access_tokens_project_id", "project_id"),
        Index("idx_access_tokens_agent", "agent_name"),
        Index("idx_access_tokens_status", "status"),
        Index("idx_access_tokens_expires", "expires_at"),
    )

    token_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    token_suffix: Mapped[str] = mapped_column(String(4), nullable=False, server_default="")
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    grantor_id: Mapped[str] = mapped_column(Text, nullable=False)
    # ARRAY(Text()) on Postgres, JSON list on SQLite
    scopes: Mapped[list] = mapped_column(ArrayText, nullable=False, default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    single_use: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConnectorTriggerModel(Base):
    """Trigger configuration — mirrors connector_triggers table from migration 001.

    PK is ``id`` (String(36), UUID hex).  JSON columns ``filter_json`` and
    ``context_json`` use JSONType (JSONB on Postgres, JSON TEXT on SQLite).
    Upsert uses ``ON CONFLICT (id)`` (the PK).
    """

    __tablename__ = "connector_triggers"
    __table_args__ = (
        Index("ix_connector_triggers_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    poll_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filter_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    target: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    context_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Connector tables
# ---------------------------------------------------------------------------


class ConnectorCredentialModel(Base):
    """Credential record for a connector — mirrors connector_credentials table from migration 001.

    PK is ``id`` (opaque String(100)); the unique constraint
    ``(project_id, connector_name)`` is the ON CONFLICT target for upserts.
    OAuth columns sit on the same row as the api-key config.
    """

    __tablename__ = "connector_credentials"
    __table_args__ = (
        Index("ix_connector_credentials_project_id", "project_id"),
        Index(
            "ix_connector_credentials_project_connector",
            "project_id",
            "connector_name",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    connector_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    # config — JSONB on pg, JSON (TEXT) on SQLite
    config: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="not_configured")
    # OAuth2 columns
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CustomConnectorModel(Base):
    """User-defined custom connector — mirrors custom_connectors table from migration 001.

    ``name`` is the PRIMARY KEY (globally unique).  The upsert in
    ``PostgresCustomConnectorStore.save()`` uses ``index_elements=["name"]``
    to target the PK, which is the only unique constraint that migration 001
    creates on this table.  There is intentionally NO unique index on
    ``(project_id, name)`` — adding one would diverge from the migration
    schema and cause ``ON CONFLICT (project_id, name)`` to fail on a
    production database where only the migration-created schema exists.
    """

    __tablename__ = "custom_connectors"
    __table_args__ = (
        Index("ix_custom_connectors_project_id", "project_id"),
    )

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, server_default="custom")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    auth_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="api_key")
    # JSON columns
    auth_fields_json: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    mcp_command_json: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    docs_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    example_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # OAuth2 fields
    oauth_authorize_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_token_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_scopes_json: Mapped[list | None] = mapped_column(JSONType, nullable=True, default=list)
    # Event support flags
    supports_webhook: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    supports_listener: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    supports_poller: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectorCursorModel(Base):
    """Polling cursor record — mirrors connector_cursors table from migration 001.

    PK is ``id`` (autoincrement Integer); the unique constraint
    ``(project_id, connector_name, channel)`` is the ON CONFLICT target.
    """

    __tablename__ = "connector_cursors"
    __table_args__ = (
        Index("ix_connector_cursors_project_id", "project_id"),
        Index(
            "ix_connector_cursors_project_connector_channel",
            "project_id",
            "connector_name",
            "channel",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    connector_name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(255), nullable=False)
    cursor_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Context tables
# ---------------------------------------------------------------------------


class ContextDocumentModel(Base):
    """Ingested-document metadata — mirrors context_documents table from migration 001.

    PK is ``id`` (opaque Text/UUID).  The upsert in
    ``PostgresContextStore.save_document()`` uses ``index_elements=["id"]``
    to target the PK, which is the only unique constraint migration 001
    creates on this table.

    ``tags`` maps to ``ARRAY(Text())`` on PostgreSQL and to a JSON list
    on SQLite via ``ArrayText``.  The GIN index on ``tags`` is created by
    migration 001; it is replicated here so ``create_all`` on SQLite builds
    a structurally equivalent table.
    """

    __tablename__ = "context_documents"
    __table_args__ = (
        Index("idx_ctx_docs_project", "project_id"),
        Index("idx_ctx_docs_scope", "scope", "scope_id"),
        Index("idx_ctx_docs_status", "status"),
        Index("idx_ctx_docs_source", "source"),
        # GIN index on tags — postgresql_using="gin" is ignored on SQLite
        Index("idx_ctx_docs_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="upload")
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="text/plain")
    file_size_bytes: Mapped[int] = mapped_column(Integer, server_default="0")
    chunk_count: Mapped[int] = mapped_column(Integer, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    confidence: Mapped[float] = mapped_column(Float, server_default="1.0")
    freshness_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # JSONB on Postgres, JSON (TEXT) on SQLite
    # Note: "metadata" is reserved by SQLAlchemy declarative base; use metadata_ mapped to "metadata" column
    metadata_: Mapped[dict] = mapped_column("metadata", JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # ARRAY(Text()) on Postgres, JSON list on SQLite
    tags: Mapped[list] = mapped_column(ArrayText, nullable=False, default=list)


class ContextChunkModel(Base):
    """Text chunk with embedding metadata — mirrors context_chunks table from migration 001.

    PK is ``id`` (opaque Text/UUID).  The upsert (ON CONFLICT DO NOTHING) in
    ``PostgresContextStore.save_chunks()`` targets the PK ``["id"]``, which is
    the only unique constraint migration 001 creates on this table.
    FK to ``context_documents.id`` with ON DELETE CASCADE.
    """

    __tablename__ = "context_chunks"
    __table_args__ = (
        Index("idx_ctx_chunks_doc", "document_id"),
        Index("idx_ctx_chunks_project", "project_id"),
        Index("idx_ctx_chunks_hash", "content_hash"),
        Index("idx_ctx_chunks_scope", "scope", "scope_id"),
        Index("idx_ctx_chunks_importance", "importance"),
        Index("idx_ctx_chunks_accessed", "last_accessed_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("context_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, server_default="0")
    token_count: Mapped[int] = mapped_column(Integer, server_default="0")
    embedding_model: Mapped[str] = mapped_column(
        Text, server_default="text-embedding-3-small"
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(Float, server_default="0.5")
    access_count: Mapped[int] = mapped_column(Integer, server_default="0")
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # JSONB on Postgres, JSON (TEXT) on SQLite
    # Note: "metadata" is reserved by SQLAlchemy declarative base; use metadata_ mapped to "metadata" column
    metadata_: Mapped[dict] = mapped_column("metadata", JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Harness tables (from migration 001, section 006)
# ---------------------------------------------------------------------------


class HarnessPolicyModel(Base):
    """Routing policy rule — mirrors harness_policies table from migration 001.

    PK is ``id`` (Text/UUID).  Individual columns for every policy field;
    scope columns (org_id, team_id, project_id, user_id) are nullable.
    The composite index ``ix_harness_policies_scope`` covers the four scope
    columns — upserts target the PK ``["id"]``.

    JSON columns ``tier_overrides``, ``blocked_models``, ``allowed_models``
    use JSONType (JSONB on Postgres, TEXT/JSON on SQLite).
    """

    __tablename__ = "harness_policies"
    __table_args__ = (
        Index("ix_harness_policies_scope", "org_id", "team_id", "project_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, server_default="")
    # Scope columns — all nullable (global policy has all None)
    org_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    team_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # JSON columns — JSONB on Postgres, TEXT/JSON on SQLite
    tier_overrides: Mapped[dict] = mapped_column(JSONType, default=dict)
    blocked_models: Mapped[list] = mapped_column(JSONType, default=list)
    allowed_models: Mapped[list] = mapped_column(JSONType, default=list)
    max_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    force_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    allow_override: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class HarnessKeyModel(Base):
    """Harness API key — mirrors harness_keys table from migration 001.

    PK is ``id`` (Text/UUID); unique constraint on ``key_hash``.
    The upsert in ``PostgresHarnessStore.create_key()`` targets the PK ``["id"]``.
    ``allowed_models`` is JSONB on Postgres / TEXT/JSON on SQLite.
    Timestamps are stored as timezone-aware DateTime (the model uses Unix float;
    the store converts on read/write).
    """

    __tablename__ = "harness_keys"
    __table_args__ = (
        Index("ix_harness_keys_org_id", "org_id"),
        Index("ix_harness_keys_user_id", "user_id"),
        Index("ix_harness_keys_key_hash", "key_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    key_suffix: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    org_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    team_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSONB on Postgres, TEXT/JSON on SQLite
    allowed_models: Mapped[list] = mapped_column(JSONType, default=list)
    max_budget_daily_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_budget_monthly_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Stored as DateTime; converted to/from Unix float in the store layer
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HarnessSpendModel(Base):
    """Per-request spend record — mirrors harness_spend table from migration 001.

    PK is ``id`` (Text/UUID).  No upsert — rows are always inserted.
    ``key_id`` FK references ``harness_keys.id`` with ON DELETE SET NULL.
    Timestamps stored as DateTime; the store converts to/from Unix float.
    """

    __tablename__ = "harness_spend"
    __table_args__ = (
        Index("ix_harness_spend_user_id", "user_id"),
        Index("ix_harness_spend_org_id", "org_id"),
        Index("ix_harness_spend_timestamp", "timestamp"),
        Index("ix_harness_spend_model_used", "model_used"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    team_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    org_id: Mapped[str] = mapped_column(Text, server_default="default")
    model_requested: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    complexity_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, server_default="0.0")
    latency_ms: Mapped[float] = mapped_column(Float, server_default="0.0")
    policy_applied: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FK to harness_keys.id; nullable (key may be deleted)
    key_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("harness_keys.id", ondelete="SET NULL"),
        nullable=True,
    )


class HarnessAuditModel(Base):
    """Audit event — mirrors harness_audit table from migration 001.

    PK is ``id`` (Text/UUID).  No upsert — rows are always inserted.
    ``details`` is JSONB on Postgres / TEXT/JSON on SQLite.
    Timestamps stored as DateTime; the store converts to/from Unix float.
    """

    __tablename__ = "harness_audit"
    __table_args__ = (
        Index("ix_harness_audit_timestamp", "timestamp"),
        Index("ix_harness_audit_event_type", "event_type"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    org_id: Mapped[str] = mapped_column(Text, server_default="default")
    # JSONB on Postgres, TEXT/JSON on SQLite
    details: Mapped[dict] = mapped_column(JSONType, default=dict)


# ---------------------------------------------------------------------------
# Notification tables — notification_channels, notification_triggers,
#                       notification_history  (migration 001)
# ---------------------------------------------------------------------------


class NotificationChannelModel(Base):
    """Channel configuration — mirrors notification_channels table.

    PK: ``id`` (Integer autoincrement).
    Unique constraint: (project_id, channel_type) — upsert index target.
    ``config`` stores non-core channel settings as JSON/JSONB.
    """

    __tablename__ = "notification_channels"
    __table_args__ = (UniqueConstraint("project_id", "channel_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    # JSONB on Postgres, TEXT/JSON on SQLite
    config: Mapped[dict] = mapped_column(JSONType, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class NotificationTriggerModel(Base):
    """Trigger routing — mirrors notification_triggers table.

    PK: ``id`` (Integer autoincrement).
    Unique constraint: (project_id, trigger, channel_type) — upsert index target.
    """

    __tablename__ = "notification_triggers"
    __table_args__ = (UniqueConstraint("project_id", "trigger", "channel_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class NotificationHistoryModel(Base):
    """Notification history record — mirrors notification_history table.

    PK: ``id`` (Integer autoincrement).  No unique constraint; rows are
    always inserted (append-only).  Indexed on project_id, trigger,
    created_at per migration 001.
    """

    __tablename__ = "notification_history"
    __table_args__ = (
        Index("ix_notification_history_project_id", "project_id"),
        Index("ix_notification_history_trigger", "trigger"),
        Index("ix_notification_history_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(Text, server_default="info")
    agent_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, server_default="false")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Sealed audit events (migration 003)
# ---------------------------------------------------------------------------


class SealedAuditEventModel(Base):
    """Sealed audit event — mirrors sealed_audit_events table from migration 003.

    PK is ``id`` (BigInteger autoincrement, append-only — no ON CONFLICT).
    ``details`` is JSONB on Postgres / TEXT/JSON on SQLite via ``JSONType``.
    Three indexes mirror migration 003:
      idx_sealed_audit_recent  — (event_type, created_at DESC)
      idx_sealed_audit_profile — (profile_id, created_at DESC)
      idx_sealed_audit_run     — (run_id, created_at DESC) WHERE run_id IS NOT NULL
    """

    __tablename__ = "sealed_audit_events"
    __table_args__ = (
        Index("idx_sealed_audit_recent", "event_type", "created_at"),
        Index("idx_sealed_audit_profile", "profile_id", "created_at"),
        Index(
            "idx_sealed_audit_run",
            "run_id",
            "created_at",
            sqlite_where=text("run_id IS NOT NULL"),
            postgresql_where=text("run_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSONB on Postgres (migration 003 uses postgresql.JSONB()); JSON/TEXT on SQLite.
    details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Directive evaluations + pending approvals (migration 008)
# ---------------------------------------------------------------------------


class DirectiveEvaluationModel(Base):
    """Directive evaluation record — mirrors directive_evaluations from migration 008.

    PK is ``id`` (BigInteger autoincrement, append-only).
    ``details`` mirrors migration 008's ``sa.JSON().with_variant(JSONB, "postgresql")``
    which is equivalent to ``JSONType``.
    Three indexes mirror migration 008:
      idx_directive_eval_recent   — (event_type, created_at DESC)
      idx_directive_eval_run      — (run_id, created_at DESC)
      idx_directive_eval_decision — (decision_id, created_at DESC) WHERE decision_id IS NOT NULL
    """

    __tablename__ = "directive_evaluations"
    __table_args__ = (
        Index("idx_directive_eval_recent", "event_type", "created_at"),
        Index("idx_directive_eval_run", "run_id", "created_at"),
        Index(
            "idx_directive_eval_decision",
            "decision_id",
            "created_at",
            sqlite_where=text("decision_id IS NOT NULL"),
            postgresql_where=text("decision_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    decision_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_name: Mapped[str] = mapped_column(Text, nullable=False)
    policy_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PendingDirectiveApprovalModel(Base):
    """Pending directive approval — mirrors pending_directive_approvals from migration 008.

    PK is ``id`` (BigInteger autoincrement).
    ``decision_id`` is UNIQUE (migration 008 ``unique=True``).
    ``triggering_signal`` and ``proposed_action`` use JSONType (JSONB on Postgres,
    JSON/TEXT on SQLite), mirroring migration 008's
    ``sa.JSON().with_variant(JSONB, "postgresql")``.
    Two indexes mirror migration 008:
      idx_directive_approvals_pending — (status, requested_at) WHERE status = 'pending'
      idx_directive_approvals_run     — (run_id, requested_at DESC)
    """

    __tablename__ = "pending_directive_approvals"
    __table_args__ = (
        Index(
            "idx_directive_approvals_pending",
            "status",
            "requested_at",
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
        Index("idx_directive_approvals_run", "run_id", "requested_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_name: Mapped[str] = mapped_column(Text, nullable=False)
    policy_id: Mapped[str] = mapped_column(Text, nullable=False)
    triggering_signal: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    proposed_action: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# Multi-tenancy / RBAC (W0): org -> projects, users, memberships, invitations.
# Tenancy model: one ORG (shared umbrella) -> many isolated PROJECTS.
# Tenant-scoped resources carry project_id (NULL = org-shared). The hard
# isolation boundary is the project; cross-project access must 404.
# See sagewai/atelier docs/superpowers/specs/2026-06-07-multitenancy-rbac-w0-rfc.md
# ---------------------------------------------------------------------------

# Reused across membership + invitation: role namespace must match project_id
# nullability (org:* <-> org-level, project:* <-> project-level).
_ROLE_SCOPE_CHECK = (
    "(project_id IS NULL AND role IN ('org:owner','org:admin','org:member')) OR "
    "(project_id IS NOT NULL AND role IN ('project:admin','project:member','project:viewer'))"
)


class OrgModel(Base):
    """The single organisation (company / install) — the shared umbrella."""

    __tablename__ = "org"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    contact_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="UTC")
    settings: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    master_key_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AccountModel(Base):
    """A user account belonging to the org (table ``user_account`` — ``user``
    is reserved in PostgreSQL; class is ``AccountModel`` to avoid colliding
    with the legacy unused ``UserModel``/``users`` scaffold from migration 001)."""

    __tablename__ = "user_account"
    __table_args__ = (
        Index("ux_user_account_org_email", "org_id", "email", unique=True),
        # Composite-FK target for membership/invitation/session (org_id, *_id).
        UniqueConstraint("org_id", "id", name="uq_user_account_org_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, ForeignKey("org.id"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_salt: Mapped[str | None] = mapped_column(Text, nullable=True)
    oidc_sub: Mapped[str | None] = mapped_column(Text, nullable=True)
    oidc_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectModel(Base):
    """An isolated project (a.k.a. department / product) under the org."""

    __tablename__ = "project"
    __table_args__ = (
        Index("ux_project_org_slug", "org_id", "slug", unique=True),
        UniqueConstraint("org_id", "id", name="uq_project_org_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, ForeignKey("org.id"), nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False, default="production")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    settings: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    data_key_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MembershipModel(Base):
    """user <-> (org | project) role. project_id NULL = org-level membership."""

    __tablename__ = "membership"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "user_id"], ["user_account.org_id", "user_account.id"], ondelete="CASCADE"
        ),
        # MATCH SIMPLE (default): NULL project_id rows (org-level) are exempt.
        ForeignKeyConstraint(
            ["org_id", "project_id"], ["project.org_id", "project.id"], ondelete="CASCADE"
        ),
        # One org-level membership per user; one membership per (user, project).
        Index(
            "ux_membership_org",
            "user_id",
            "org_id",
            unique=True,
            sqlite_where=text("project_id IS NULL"),
            postgresql_where=text("project_id IS NULL"),
        ),
        Index(
            "ux_membership_proj",
            "user_id",
            "org_id",
            "project_id",
            unique=True,
            sqlite_where=text("project_id IS NOT NULL"),
            postgresql_where=text("project_id IS NOT NULL"),
        ),
        CheckConstraint(_ROLE_SCOPE_CHECK, name="ck_membership_role_scope"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    org_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OrgInvitationModel(Base):
    """A pending invitation to join the org (project_id NULL) or a project.

    Class is ``OrgInvitationModel`` to avoid colliding with the legacy unused
    ``InvitationModel``/``invitations`` scaffold from migration 001."""

    __tablename__ = "invitation"
    __table_args__ = (
        ForeignKeyConstraint(["org_id", "invited_by"], ["user_account.org_id", "user_account.id"]),
        ForeignKeyConstraint(["org_id", "project_id"], ["project.org_id", "project.id"]),
        Index("ux_invitation_token_hash", "token_hash", unique=True),
        CheckConstraint(_ROLE_SCOPE_CHECK, name="ck_invitation_role_scope"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    invited_by: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserSessionModel(Base):
    """A per-user auth session (token hashed at rest), for multi-tenant login."""

    __tablename__ = "user_session"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "user_id"], ["user_account.org_id", "user_account.id"], ondelete="CASCADE"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEventModel(Base):
    """A durable, hash-chained, per-tenant audit event (W8).

    Each row belongs to a **per-``(org_id, project_id)`` append-only hash
    chain** (``project_id IS NULL`` is the org-level chain). ``seq`` is the
    event's position within its chain; ``hash = sha256(prev_hash ||
    canonical_json(event))`` links it to its predecessor, making the chain
    end-to-end tamper-evident (see :mod:`sagewai.admin.tenant_audit`).

    **Audit does NOT inherit** the org-shared rule (W0 RFC §3): a project chain
    read returns only ``project_id = P`` and is never combined with the org-level
    ``project_id IS NULL`` chain — each tenant has an independent log.

    Integrity at the DB (RFC §6):
    - composite FK ``(org_id, project_id) -> project(org_id, id)`` (MATCH SIMPLE,
      so org-level ``NULL``-project rows are exempt) keeps a row from claiming one
      org while pointing at another org's project;
    - NULL-safe **partial unique** sequence indexes so no two events share a
      ``seq`` within a chain (and a chain can't fork or duplicate a position):
      ``ux_audit_seq_proj`` over ``(org_id, project_id, seq)`` for project chains
      and ``ux_audit_seq_org`` over ``(org_id, seq)`` for the org-level chain.

    Append-only: rows are only ever inserted (no UPDATE/DELETE in the emitter).
    PK is ``id`` (BigInteger autoincrement — BIGSERIAL on Postgres).
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        # Composite FK: a project event must reference a project in its own org.
        # MATCH SIMPLE (default): NULL project_id (org-level) rows are exempt.
        ForeignKeyConstraint(["org_id", "project_id"], ["project.org_id", "project.id"]),
        # One seq per (org, project) chain — partial so NULL project_id rows go to
        # the org-level index instead (Postgres treats NULL as distinct, so a plain
        # unique would let the org chain duplicate a seq).
        Index(
            "ux_audit_seq_proj",
            "org_id",
            "project_id",
            "seq",
            unique=True,
            sqlite_where=text("project_id IS NOT NULL"),
            postgresql_where=text("project_id IS NOT NULL"),
        ),
        Index(
            "ux_audit_seq_org",
            "org_id",
            "seq",
            unique=True,
            sqlite_where=text("project_id IS NULL"),
            postgresql_where=text("project_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(Text, ForeignKey("org.id"), nullable=False)
    # NULL = the org-level chain; set = a project chain.
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Real acting user; nullable for system-originated events.
    actor_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSONB on Postgres, JSON/TEXT on SQLite. "metadata" is reserved on the
    # declarative Base, so the attribute is metadata_ -> column "metadata".
    metadata_: Mapped[dict] = mapped_column("metadata", JSONType, nullable=False, default=dict)
    # Position within this row's (org, project) chain (starts at 1).
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prev_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditChainHeadModel(Base):
    """The tip checkpoint for one ``(org_id, project_id)`` audit chain (W8).

    Exactly one row per chain (``project_id IS NULL`` = the org-level chain),
    holding the **last assigned ``seq``** and the **tip ``hash``**. It is the
    authority on a chain's expected length and tip, which gives the emitter two
    properties an in-table hash chain alone cannot:

    - **Tail-deletion detection.** Deleting the last event (or the whole chain)
      leaves no gap in ``audit_event``, so a re-walk of the surviving rows looks
      valid. ``verify_chain`` compares the walked tip against this checkpoint and
      flags a shorter-than-expected chain.
    - **Append serialisation.** ``append`` advances this row under optimistic
      concurrency (``UPDATE ... WHERE seq = <observed>``); together with the
      per-chain unique ``seq`` index on ``audit_event`` it lets concurrent
      writers to the same chain retry instead of losing events.

    ``id`` is a surrogate PK; one-head-per-chain is enforced by NULL-safe partial
    unique indexes (``ux_audit_head_proj`` / ``ux_audit_head_org``) mirroring the
    ``audit_event`` sequence indexes. Composite FK keeps a head from pointing at
    another org's project.
    """

    __tablename__ = "audit_chain_head"
    __table_args__ = (
        ForeignKeyConstraint(["org_id", "project_id"], ["project.org_id", "project.id"]),
        Index(
            "ux_audit_head_proj",
            "org_id",
            "project_id",
            unique=True,
            sqlite_where=text("project_id IS NOT NULL"),
            postgresql_where=text("project_id IS NOT NULL"),
        ),
        Index(
            "ux_audit_head_org",
            "org_id",
            unique=True,
            sqlite_where=text("project_id IS NULL"),
            postgresql_where=text("project_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(Text, ForeignKey("org.id"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Last assigned seq and the tip hash for this chain.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkerModel(Base):
    """Fleet worker — mirrors the `workers` table (001_initial + 018).

    Shared with the core workflow-worker system. Fleet rows are written with
    ``status='fleet'`` so the core load balancer (which selects ``status='active'``)
    never picks them. ``metadata`` is mapped to ``metadata_`` (``metadata`` is
    reserved on DeclarativeBase).
    """

    __tablename__ = "workers"
    __table_args__ = (
        Index("idx_workers_pool", "pool"),
        Index("idx_workers_status", "status"),
        Index("idx_workers_project_id", "project_id"),
        Index("ix_workers_org_approval", "org_id", "approval_status"),
    )

    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)
    pool: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    labels: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, server_default="4")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    metadata_: Mapped[dict] = mapped_column("metadata", JSONType, nullable=False, default=dict)
    org_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    models_supported: Mapped[list] = mapped_column(ArrayText, nullable=False, default=list)
    models_canonical: Mapped[list] = mapped_column(ArrayText, nullable=False, default=list)
    approval_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    capabilities: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    probe_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    sdk_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # migration 018:
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True)


class EnrollmentKeyModel(Base):
    """Fleet enrollment key — mirrors the `enrollment_keys` table (001_initial)."""

    __tablename__ = "enrollment_keys"
    __table_args__ = (Index("ix_enrollment_keys_org", "org_id"),)

    id: Mapped[str] = mapped_column(UuidText, primary_key=True)  # UUID on Postgres, TEXT on SQLite
    org_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_uses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    allowed_pools: Mapped[list] = mapped_column(ArrayText, nullable=False, default=list)
    allowed_models: Mapped[list] = mapped_column(ArrayText, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class FleetTaskModel(Base):
    """Durable fleet task queue (B1). One row per enqueued run.

    `status` is constrained to the queue lifecycle; `org_id` is NOT NULL (tenant
    isolation — the durable store requires an exact org, unlike the in-memory dev
    helper). The claim index covers the exact filter + FIFO order.
    """

    __tablename__ = "fleet_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','claimed','completed','failed')",
            name="ck_fleet_tasks_status",
        ),
        Index("ix_fleet_tasks_claim", "status", "org_id", "project_id", "pool", "created_at"),
        Index("ix_fleet_tasks_scope", "org_id", "project_id", "created_at"),
        Index("ix_fleet_tasks_lease", "status", "lease_expires_at"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    pool: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    worker_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
