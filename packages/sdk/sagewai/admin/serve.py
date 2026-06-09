# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Complete admin API server factory.

``create_admin_serve_app`` returns a fully-configured FastAPI application
with all the routes the Next.js admin frontend expects.  The CLI
command ``sagewai admin serve`` delegates to this factory.

The server uses :class:`AdminStateFile` for persistence (file-backed,
zero external deps).  For production, replace with Postgres-backed
stores.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.responses import Response, StreamingResponse

from sagewai import __version__ as _SDK_VERSION
from sagewai.admin.authz import require_in_project_scope, require_org_admin
from sagewai.admin.state_file import SHARED_ONLY, AdminStateFile, _slugify

logger = logging.getLogger("sagewai.admin")

# ── Built-in data ────────────────────────────────────────────────────

_STRATEGIES = [
    {"id": "single", "name": "Single Pass", "description": "One LLM call, no iteration."},
    {"id": "react", "name": "ReAct", "description": "Reason-Act-Observe loop with tool calling."},
    {"id": "chain_of_thought", "name": "Chain of Thought", "description": "Step-by-step reasoning in a single pass."},
    {"id": "tree_of_thoughts", "name": "Tree of Thoughts", "description": "Explore multiple reasoning branches in parallel."},
    {"id": "lats", "name": "LATS", "description": "Monte Carlo tree search over reasoning trajectories."},
    {"id": "debate", "name": "Debate", "description": "Multiple personas argue, a judge synthesizes."},
    {"id": "planning", "name": "Plan and Execute", "description": "Decompose goal into subtasks, execute step by step."},
    {"id": "reflexion", "name": "Reflexion", "description": "LLM-as-judge with reflective critique accumulation."},
    {"id": "self_correction", "name": "Self-Correction", "description": "Detect and fix errors with failure exemplars."},
    {"id": "majority_vote", "name": "Majority Vote", "description": "Generate N responses, pick the most consistent."},
    {"id": "evaluator_optimizer", "name": "Evaluator-Optimizer", "description": "Generate → evaluate → revise until approved."},
    {"id": "routing", "name": "Routing", "description": "Route to specialist agents by intent."},
]

_PRESETS = [
    {"name": "Balanced", "temperature": 0.7, "top_p": 0.95},
    {"name": "Creative", "temperature": 1.0, "top_p": 0.98},
    {"name": "Precise", "temperature": 0.2, "top_p": 0.9},
    {"name": "Code", "temperature": 0.1, "top_p": 0.95},
    {"name": "Conversational", "temperature": 0.8, "top_p": 0.95},
]

_CAPABILITIES = {
    "tools": [
        {"id": "web_search", "name": "Web Search", "description": "Search the internet for real-time information."},
        {"id": "calculator", "name": "Calculator", "description": "Evaluate mathematical expressions."},
        {"id": "code_interpreter", "name": "Code Interpreter", "description": "Execute Python code in a sandbox."},
        {"id": "file_reader", "name": "File Reader", "description": "Read and parse file contents."},
        {"id": "weather_lookup", "name": "Weather Lookup", "description": "Get current weather by location."},
        {"id": "knowledge_base", "name": "Knowledge Base", "description": "Search internal documentation."},
        {"id": "ticket_lookup", "name": "Ticket Lookup", "description": "Search support tickets and issues."},
        {"id": "send_email", "name": "Send Email", "description": "Send an email via configured provider (Resend/SendGrid/Postmark). Args: to, subject, body."},
        {"id": "send_slack", "name": "Send Slack Message", "description": "Post a message to a Slack channel via webhook. Args: message, channel (optional)."},
    ],
    # mcp_servers is computed at request time from registered MCP connections
    # (see ``playground_capabilities``); the empty default here is a safety net
    # if the live lookup fails.
    "mcp_servers": [],
    "memory": [
        {"id": "vector", "name": "Vector Memory", "description": "Semantic search over past conversations."},
        {"id": "graph", "name": "Knowledge Graph", "description": "Entity and relationship storage."},
    ],
    "guardrails": [
        {"id": "pii_filter", "name": "PII Filter", "description": "Detect and redact personal information."},
        {"id": "hallucination_check", "name": "Hallucination Check", "description": "Flag unsupported claims."},
        {"id": "content_filter", "name": "Content Filter", "description": "Block forbidden words and patterns."},
        {"id": "output_schema", "name": "Output Schema", "description": "Validate JSON schema compliance."},
        {"id": "token_budget", "name": "Token Budget", "description": "Enforce cost limits per request."},
    ],
    "strategies": [{"id": s["id"], "name": s["name"], "description": s["description"]} for s in _STRATEGIES],
}

# Agent templates — imported from the CLI module's existing list
_AGENT_TEMPLATES: list[dict[str, Any]] = []


def _tenant_org_payload(org: dict[str, Any]) -> dict[str, Any]:
    settings = org.get("settings") or {}
    return {
        "id": org.get("id"),
        "org_name": org.get("name", ""),
        "org_slug": org.get("slug", ""),
        "app_url": settings.get("app_url", ""),
        "contact_email": org.get("contact_email") or "",
        "timezone": org.get("timezone", "UTC"),
        "industry": settings.get("industry", ""),
        "team_size": settings.get("team_size", ""),
        "completed_at": org.get("created_at"),
    }


def _tenant_project_payload(project: dict[str, Any]) -> dict[str, Any]:
    settings = project.get("settings") or {}
    return {
        "id": project.get("id"),
        "slug": project.get("slug", ""),
        "name": project.get("name", ""),
        "environment": project.get("environment", "production"),
        "allowed_origins": settings.get("allowed_origins", ""),
        "default_model": settings.get("default_model"),
        "status": project.get("status", "active"),
        "created_at": project.get("created_at"),
        "updated_at": project.get("updated_at"),
    }


def _tenant_user_payload(
    user: dict[str, Any],
    ctx: Any,
    memberships: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    roles = sorted(ctx.roles) if ctx is not None else []
    public_role = "admin" if {"org:owner", "org:admin"} & set(roles) else "member"
    return {
        "id": user.get("id"),
        "email": user.get("email", ""),
        "display_name": user.get("name") or "",
        "name": user.get("name") or "",
        "avatar_url": None,
        "role": public_role,
        "roles": roles,
        "org_id": user.get("org_id"),
        "project_id": getattr(ctx, "project_id", None),
        "memberships": memberships or [],
    }


def _resolve_database_url() -> str | None:
    """Accept either env var; SAGEWAI_DATABASE_URL wins over DATABASE_URL."""
    return os.environ.get("SAGEWAI_DATABASE_URL") or os.environ.get("DATABASE_URL") or None


def _load_templates() -> list[dict[str, Any]]:
    """Lazy-load templates to avoid circular imports."""
    global _AGENT_TEMPLATES
    if not _AGENT_TEMPLATES:
        # Import the template list built in cli/admin.py
        # For now, define inline — will be refactored to a shared module
        _AGENT_TEMPLATES = [
            {
                "id": "hello-agent",
                "name": "Hello Agent",
                "description": "Your first Sagewai agent in 5 lines. Basic Agent → run() → response loop.",
                "system_prompt": "You are a helpful AI assistant powered by Sagewai.",
                "model": "gpt-4o-mini", "temperature": 0.7, "strategy": "single",
                "tools": [], "mcp_servers": [], "memory_backends": [], "guardrails": [],
                "category": "Getting Started",
            },
            {
                "id": "tool-agent",
                "name": "Tool-Augmented Agent",
                "description": "Demonstrates @tool decorator — custom Python functions as agent superpowers.",
                "system_prompt": "You are a helpful assistant with access to tools. Use them for real-time data, calculations, or external lookups.",
                "model": "gpt-4o", "temperature": 0.3, "strategy": "react",
                "tools": ["web_search", "calculator", "weather_lookup"],
                "mcp_servers": [], "memory_backends": [], "guardrails": [],
                "category": "Tool Calling",
            },
            {
                "id": "mcp-connected",
                "name": "MCP-Connected Agent",
                "description": "Connect to any MCP server and use its tools. Universal tool standard.",
                "system_prompt": "You are an agent connected to external MCP tool servers. Discover and use tools to fulfill requests.",
                "model": "gpt-4o", "temperature": 0.3, "strategy": "react",
                "tools": [], "mcp_servers": [],
                "memory_backends": [], "guardrails": [],
                "category": "MCP Integration",
            },
            {
                "id": "memory-agent",
                "name": "Persistent Memory Agent",
                "description": "Uses vector memory for semantic search and graph memory for entity relationships. Cross-session recall.",
                "system_prompt": "You are a knowledgeable assistant with persistent memory. Reference past conversations and tracked entities.",
                "model": "gpt-4o", "temperature": 0.4, "strategy": "react",
                "tools": [], "mcp_servers": [], "memory_backends": ["vector", "graph"],
                "guardrails": [],
                "category": "Memory & Knowledge",
            },
            {
                "id": "rag-researcher",
                "name": "RAG Research Assistant",
                "description": "Retrieval-Augmented Generation with context engine. Ingests docs, embeds, retrieves before every LLM call.",
                "system_prompt": "You are a research assistant. Search your knowledge base first, cite sources, distinguish facts from reasoning.",
                "model": "gpt-4o", "temperature": 0.2, "strategy": "single",
                "tools": ["file_reader"], "mcp_servers": [], "memory_backends": ["vector"],
                "guardrails": ["hallucination_check"],
                "category": "Memory & Knowledge",
            },
            {
                "id": "react-agent",
                "name": "ReAct Reasoning Agent",
                "description": "Classic Reason-Act-Observe loop (Yao et al. 2023). Iterates until done.",
                "system_prompt": "You are a methodical problem solver. Think → Act → Observe → Repeat.",
                "model": "gpt-4o", "temperature": 0.3, "strategy": "react",
                "tools": ["web_search", "calculator"], "mcp_servers": [],
                "memory_backends": [], "guardrails": [],
                "category": "Strategies",
            },
            {
                "id": "tree-of-thoughts",
                "name": "Tree of Thoughts Agent",
                "description": "Explores multiple reasoning branches in parallel, scores and prunes (Yao et al. 2024).",
                "system_prompt": "Generate multiple solution approaches, evaluate critically, select the strongest path.",
                "model": "gpt-4o", "temperature": 0.7, "strategy": "tree_of_thoughts",
                "tools": [], "mcp_servers": [], "memory_backends": [], "guardrails": [],
                "category": "Strategies",
            },
            {
                "id": "debate-agent",
                "name": "Debate Strategy Agent",
                "description": "Multi-perspective reasoning — debater personas argue, a judge synthesizes.",
                "system_prompt": "Present multiple perspectives, challenge with evidence, synthesize the strongest arguments.",
                "model": "gpt-4o", "temperature": 0.6, "strategy": "debate",
                "tools": [], "mcp_servers": [], "memory_backends": [], "guardrails": [],
                "category": "Strategies",
            },
            {
                "id": "self-correcting",
                "name": "Self-Correcting Agent",
                "description": "Detects errors and retries with PALADIN-style 1-shot correction.",
                "system_prompt": "Check your work. If errors found, correct before presenting the final answer.",
                "model": "gpt-4o", "temperature": 0.3, "strategy": "self_correction",
                "tools": ["code_interpreter"], "mcp_servers": [], "memory_backends": [], "guardrails": [],
                "category": "Strategies",
            },
            {
                "id": "plan-and-execute",
                "name": "Plan-and-Execute Agent",
                "description": "Decomposes goals into steps, executes each, replans when needed.",
                "system_prompt": "Decompose → Execute → Reflect → Replan if needed.",
                "model": "gpt-4o", "temperature": 0.3, "strategy": "planning",
                "tools": ["web_search", "file_reader", "code_interpreter"],
                "mcp_servers": [], "memory_backends": [], "guardrails": [],
                "category": "Strategies",
            },
            {
                "id": "safe-enterprise",
                "name": "Enterprise-Safe Agent",
                "description": "Full safety stack: PII, hallucination, content filter, schema, budget.",
                "system_prompt": "Never expose PII. Cite sources. Stay within approved topics. Say 'I don't know' when uncertain.",
                "model": "gpt-4o", "temperature": 0.3, "strategy": "single",
                "tools": [], "mcp_servers": [], "memory_backends": ["vector"],
                "guardrails": ["pii_filter", "hallucination_check", "content_filter", "output_schema", "token_budget"],
                "category": "Enterprise Safety",
            },
            {
                "id": "directive-agent",
                "name": "Directive-Powered Agent",
                "description": "@context, @memory, @agent sigils — enables small local models to use full infrastructure.",
                "system_prompt": "Use @context for documents, @memory for facts, @agent to delegate subtasks.",
                "model": "gpt-4o-mini", "temperature": 0.4, "strategy": "single",
                "tools": [], "mcp_servers": [], "memory_backends": ["vector", "graph"], "guardrails": [],
                "category": "Directives",
            },
            {
                "id": "research-pipeline",
                "name": "Research → Write → Edit Pipeline",
                "description": "3-agent workflow: Researcher → Writer → Editor. Crash-recoverable.",
                "system_prompt": "Coordinate: Researcher gathers facts, Writer drafts, Editor polishes.",
                "model": "gpt-4o", "temperature": 0.4, "strategy": "planning",
                "tools": ["web_search", "file_reader"], "mcp_servers": [],
                "memory_backends": ["vector"], "guardrails": ["hallucination_check"],
                "category": "Workflows",
            },
            {
                "id": "smart-router",
                "name": "Smart Model Router",
                "description": "Routes by complexity: simple→local/Haiku ($0), medium→Sonnet, complex→Opus/GPT-4o.",
                "system_prompt": "Analyze complexity, route to most cost-effective capable model.",
                "model": "auto", "temperature": 0.3, "strategy": "routing",
                "tools": [], "mcp_servers": [], "memory_backends": [],
                "guardrails": ["token_budget"],
                "category": "Model Routing",
            },
            {
                "id": "local-first",
                "name": "Local-First Agent",
                "description": "Simple tasks → local LLM ($0/token), complex → cloud. Hybrid architecture.",
                "system_prompt": "Handle simple requests locally, escalate complex to cloud.",
                "model": "auto", "temperature": 0.5, "strategy": "routing",
                "tools": [], "mcp_servers": [], "memory_backends": [],
                "guardrails": ["token_budget"],
                "category": "Model Routing",
            },
            {
                "id": "fleet-worker",
                "name": "Fleet Worker Agent",
                "description": "Distributed agent across fleet workers. Enrollment, dispatch, heartbeat.",
                "system_prompt": "Report capabilities, accept dispatched tasks, return results reliably.",
                "model": "gpt-4o-mini", "temperature": 0.3, "strategy": "single",
                "tools": [], "mcp_servers": [], "memory_backends": [], "guardrails": [],
                "category": "Fleet & Distribution",
            },
            {
                "id": "harness-proxy",
                "name": "IDE Cost Governor",
                "description": "Enterprise proxy for Claude Code, Cursor, Copilot. Budget + smart routing.",
                "system_prompt": "Intercept LLM requests, classify complexity, route optimally, enforce budgets.",
                "model": "auto", "temperature": 0.3, "strategy": "routing",
                "tools": [], "mcp_servers": [], "memory_backends": [],
                "guardrails": ["token_budget"],
                "category": "IDE Governance",
            },
            {
                "id": "customer-support",
                "name": "Customer Support Agent",
                "description": "Memory-backed support with PII protection and escalation rules.",
                "system_prompt": "Be empathetic. Use memory for past interactions. Redact PII. Escalate when: frustrated, can't resolve in 3 turns, billing issue.",
                "model": "gpt-4o-mini", "temperature": 0.5, "strategy": "react",
                "tools": ["ticket_lookup", "knowledge_base"], "mcp_servers": [],
                "memory_backends": ["vector"], "guardrails": ["pii_filter", "content_filter"],
                "category": "Domain-Specific",
            },
            {
                "id": "legal-reviewer",
                "name": "Legal Document Reviewer",
                "description": "RAG over policy corpus. Flags risky clauses, missing terms.",
                "system_prompt": "Review contracts against standard terms. Flag: non-standard liability, missing IP assignment, auto-renewal traps.",
                "model": "gpt-4o", "temperature": 0.1, "strategy": "single",
                "tools": ["file_reader"], "mcp_servers": [],
                "memory_backends": ["vector"],
                "guardrails": ["pii_filter", "hallucination_check"],
                "category": "Domain-Specific",
            },
        ]
    return _AGENT_TEMPLATES


# ── App factory ──────────────────────────────────────────────────────


def _register_optional_backends(sf: AdminStateFile) -> None:
    """Register Sealed-ii external Identity backends if configured.

    Reads admin-state.sealed.vault.* and conditionally imports + registers
    the VaultBackend. ImportError (hvac missing) becomes a clear startup
    error with remediation. No-op when Vault is disabled or unset.
    """
    vault_cfg = sf.get_vault_config()
    if not vault_cfg.get("enabled"):
        return
    try:
        from sagewai.sealed.vault_backend import build_vault_backend_from_config
    except ImportError as e:
        raise RuntimeError(
            "sealed.vault.enabled=true but hvac is not installed. "
            "Run: pip install sagewai[vault]"
        ) from e
    backend = build_vault_backend_from_config(vault_cfg)
    if backend is None:
        return
    from sagewai.sealed.refs import register_backend, set_default_scheme
    register_backend(backend)
    default_scheme = sf.get_sealed_config().get("default_scheme")
    if default_scheme:
        set_default_scheme(default_scheme)


def _build_subscription_manager():
    """Construct the process-wide SubscriptionManager (admin lifespan owns it).

    A factory so tests can drive the build + teardown without the full app.
    Hard caps come from the manager's own defaults; per-connection /
    per-subscription bounds lower them at subscribe time.
    """
    from sagewai.connections.subscriptions.manager import SubscriptionManager

    return SubscriptionManager()


def create_admin_serve_app(
    sf: AdminStateFile,
    *,
    version: str = _SDK_VERSION,
    identity_store: Any = None,
    provider_store: Any = None,
    agent_store: Any = None,
    connection_store: Any = None,
    run_store: Any = None,
    prompt_log_store: Any = None,
) -> FastAPI:
    """Create the complete admin API server.

    Parameters
    ----------
    sf:
        The state-file store instance.
    version:
        SDK version string (injected by the CLI).
    identity_store:
        Optional multi-tenant IdentityStore. When omitted, the AuthMiddleware
        lazily constructs one from the process engine in multi-tenant mode. Inject
        an explicit store for deterministic tests/wiring.
    provider_store, agent_store, connection_store:
        Optional pre-built tenant resource stores (multi-tenant mode). When all
        are omitted the lifespan builds them from the process engine; inject for
        deterministic tests/wiring. Bundled on ``app.state.resource_stores``.
    """
    from sagewai import home
    home.migrate_home()

    from sagewai.admin import create_admin_router
    from sagewai.admin.analytics import (
        create_analytics_router,
    )
    from sagewai.admin.postgres_analytics import PostgresAnalyticsStore
    from sagewai.admin.state import AdminState
    from sagewai.autopilot.controller.driver import MissionDriver
    from sagewai.autopilot.controller.runner import SchedulerRunner
    from sagewai.autopilot.controller.scheduler import MissionScheduler
    from sagewai.db import factory as _db_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Start and stop the autopilot scheduler runner alongside the app."""
        scheduler = MissionScheduler()
        driver = MissionDriver(scheduler=scheduler)
        runner_interval = float(os.getenv("SAGEWAI_SCHEDULER_INTERVAL_SECONDS", "60"))
        runner = SchedulerRunner(
            scheduler=scheduler,
            driver=driver,
            interval_seconds=runner_interval,
        )
        app.state.scheduler = scheduler
        app.state.scheduler_driver = driver
        app.state.scheduler_runner = runner
        await runner.start()

        # Subscription foundation (PR1) → MQTT (PR2). Construct the
        # process-wide manager, start its idle/dead-task reaper, and expose
        # the singleton so the connections executor + the per-protocol
        # subscription routes reach it via get_subscription_manager().
        from sagewai.connections.subscriptions.manager import (
            set_subscription_manager,
        )

        sub_manager = _build_subscription_manager()
        set_subscription_manager(sub_manager)
        sub_manager.start_reaper()
        app.state.subscription_manager = sub_manager

        # Bootstrap the SQLite schema (no-op on Postgres where Alembic owns it).
        # Must run before any store reads/writes so all tables exist.
        await _db_factory.ensure_schema()

        # Build missing tenant resource stores from the process engine (multi-
        # tenant only; None in single-org). Partial DI is allowed, but every
        # missing tenant store must be filled here so routes never drift back to
        # the legacy file-store path in multi mode.
        from sagewai.admin.resource_stores import build_resource_stores
        _rs = app.state.resource_stores
        if _is_multi_tenant() and (
            _rs.provider is None
            or _rs.agent is None
            or _rs.connection is None
            or _rs.run is None
            or _rs.prompt_log is None
        ):
            _built = await build_resource_stores(identity_store)
            if _built is not None:
                app.state.resource_stores = type(_rs)(
                    provider=_rs.provider or _built.provider,
                    agent=_rs.agent or _built.agent,
                    connection=_rs.connection or _built.connection,
                    run=_rs.run or _built.run,
                    prompt_log=_rs.prompt_log or _built.prompt_log,
                )
                conn_ctx = getattr(app.state, "connections_context", None)
                active_connection_store = app.state.resource_stores.connection
                if conn_ctx is not None and active_connection_store is not None:
                    conn_ctx.store = active_connection_store
                    conn_ctx.tenant_safe = True

        # Fail closed (multi-tenant): mirror sf.require_secret_key_if_encrypted()
        # for the tenant provider table — if encrypted provider secrets exist but
        # the org master key cannot be resolved, refuse to serve rather than start
        # unable to decrypt any project's credentials.
        await _require_tenant_provider_key_if_encrypted(app.state.resource_stores.provider)

        # Wire the durable workflow checkpoint store as the process-wide
        # default for DurableWorkflow instances that don't supply their own
        # store. Tests never call configure_default_workflow_store() so they
        # continue to get InMemoryStore.
        from sagewai.core.state import configure_default_workflow_store
        _wf_store = await _db_factory.get_workflow_store()
        configure_default_workflow_store(_wf_store)

        # Wire durable vector memory when sqlite-vec is available.
        # Tests that never call create_admin_serve_app() keep the safe
        # in-process defaults (VectorMemory / InMemoryBackend).
        from sagewai.memory.sqlite_vec import sqlite_vec_available
        if sqlite_vec_available():
            from sagewai.memory.global_memory import GlobalMemory
            from sagewai.memory.global_memory_backends import SqliteVecBackend
            from sagewai.memory.rag import configure_default_vector_memory
            from sagewai.memory.sqlite_vec import SqliteVecMemory
            GlobalMemory.configure_backend(SqliteVecBackend())
            configure_default_vector_memory(
                lambda pid: SqliteVecMemory(project_id=pid)
            )
            logger.info("Vector memory: sqlite-vec (durable)")
        else:
            logger.warning(
                "sqlite-vec unavailable; vector memory is in-process "
                "(not durable across restart)"
            )

        try:
            yield
        finally:
            await sub_manager.aclose()
            set_subscription_manager(None)
            await runner.stop()
            # Clear the process-wide workflow store default on shutdown so
            # that in-process test runners (TestClient) don't leak the
            # SQLite engine into subsequent tests.
            from sagewai.core.state import configure_default_workflow_store
            configure_default_workflow_store(None)
            # Tear down the workflow store connection pool (important for
            # Postgres: closes the asyncpg pool so no connections leak).
            # Guard with try in case startup partially failed before _wf_store
            # was assigned.
            try:
                await _wf_store.close()
            except Exception:
                pass
            # Reset vector memory defaults so serve TestClient runs don't
            # leak the durable backend into subsequent tests.
            from sagewai.memory.global_memory import GlobalMemory
            from sagewai.memory.global_memory_backends import InMemoryBackend
            from sagewai.memory.rag import configure_default_vector_memory
            configure_default_vector_memory(None)
            GlobalMemory.configure_backend(InMemoryBackend())

    app = FastAPI(title="Sagewai Admin", version=version, lifespan=lifespan)

    # Tenant resource-store bundle. Injected stores (tests/DI) win; otherwise the
    # lifespan builds them once the schema exists (multi-tenant only). Attached
    # before routes so handlers reach the active store via _provider_store().
    from sagewai.admin.resource_stores import ResourceStores
    app.state.resource_stores = ResourceStores(
        provider=provider_store, agent=agent_store, connection=connection_store,
        run=run_store, prompt_log=prompt_log_store)

    # Multi-tenant: build ONE IdentityStore on the process engine and SHARE it
    # between AuthMiddleware and the resource stores when none was injected. The
    # provider store needs it to resolve per-project data keys for secret
    # encryption; the lazily-built middleware store would otherwise leave the
    # auto-built provider store with identity_store=None.
    from sagewai.admin.tenancy import is_multi_tenant as _is_multi_tenant
    if identity_store is None and _is_multi_tenant():
        from sagewai.admin.identity_store import IdentityStore
        identity_store = IdentityStore()
    app.state.identity_store = identity_store

    async def _provider_decrypted(request: Request, provider_id: str):
        """A provider with secrets decrypted, from the active store (PG or file)."""
        store = _provider_store(request)
        if store is not None:
            return await store.get_decrypted(provider_id, ctx=request.state.context)
        return sf.get_provider_decrypted(provider_id, project_id=_project_scope(request))

    async def _providers_decrypted(request: Request):
        """All in-scope providers with secrets decrypted, from the active store."""
        store = _provider_store(request)
        if store is not None:
            return await store.list_decrypted(ctx=request.state.context)
        return sf.list_providers_decrypted(project_id=_project_scope(request))

    async def _litellm_completion_kwargs(request: Request, requested_model: str) -> dict[str, Any]:
        from sagewai.admin.provider_resolution import (
            choose_provider,
            litellm_kwargs_for_provider,
        )

        providers = await _providers_decrypted(request)
        chosen = choose_provider(providers)
        ctx = getattr(request.state, "context", None)
        if chosen is None:
            if ctx is not None and getattr(ctx, "tenancy_mode", None) == "multi":
                raise RuntimeError("No LLM provider configured for this project")
            return {"model": requested_model or "gpt-4o-mini"}
        kwargs = litellm_kwargs_for_provider(chosen, requested_model=requested_model or None)
        if (
            ctx is not None
            and getattr(ctx, "tenancy_mode", None) == "multi"
            and not kwargs.get("api_key")
            and not kwargs.get("api_base")
        ):
            raise RuntimeError("Selected LLM provider has no project-scoped credentials")
        return kwargs

    async def _resolve_agent(request: Request, name: str):
        """Resolve an agent spec from the active store (PG or file) for the ctx scope."""
        store = _agent_store(request)
        if store is not None:
            return await store.get(name, ctx=request.state.context)
        return sf.get_agent(name, project_id=_project_scope(request))

    # Run state migrations before serving (versioned, locked, backed-up).
    sf.run_migrations()
    # Fail closed: if encrypted secrets exist but no master key is available,
    # refuse to start rather than silently serving nulled/ciphertext credentials.
    sf.require_secret_key_if_encrypted()

    # Auth boundary (deny-by-default). Added BEFORE CORS so CORS is the
    # OUTERMOST middleware and wraps even 401/403 responses with CORS headers.
    from sagewai.admin.auth_middleware import AuthMiddleware
    app.add_middleware(AuthMiddleware, sf=sf, identity_store=identity_store)

    # RBAC enforcement (W3) raises typed errors; map them to HTTP. TenantHidden
    # is 404 (existence-hiding, never 403); PermissionDenied is 403.
    from sagewai.admin.authz import PermissionDeniedError, TenantHiddenError

    @app.exception_handler(TenantHiddenError)
    async def _on_tenant_hidden(request: Request, exc: TenantHiddenError) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.exception_handler(PermissionDeniedError)
    async def _on_permission_denied(request: Request, exc: PermissionDeniedError) -> JSONResponse:
        return JSONResponse({"detail": "Forbidden"}, status_code=403)

    # W8 fail-closed: a sensitive write whose durable audit append failed is not
    # reported as success (503; idempotent retry reconciles).
    @app.exception_handler(_AuditUnavailableError)
    async def _on_audit_unavailable(request: Request, exc: _AuditUnavailableError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Audit log unavailable; operation not recorded — please retry"},
            status_code=503,
        )

    @app.exception_handler(_TenantStoreUnavailableError)
    async def _on_tenant_store_unavailable(
        request: Request, exc: _TenantStoreUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            {"detail": f"Tenant {exc.store_name} store unavailable"},
            status_code=503,
        )

    # W7 per-project run-rate quota (multi-tenant): one tenant can't starve others.
    app.state.run_throttle = _ProjectRunThrottle(_run_quota_limit(), _run_quota_window())

    @app.exception_handler(_QuotaExceededError)
    async def _on_quota_exceeded(request: Request, exc: _QuotaExceededError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Project quota exceeded — slow down"},
            status_code=429,
            headers={"Retry-After": str(int(_run_quota_window()))},
        )

    @app.exception_handler(_RunProjectRequiredError)
    async def _on_run_project_required(request: Request, exc: _RunProjectRequiredError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Select a project (X-Project-ID) to run"},
            status_code=409,
        )

    # CORS — env-driven allowlist (default: the local admin dev server, which
    # runs on port 3008). The admin UI calls the backend cross-origin with
    # credentials, so the origin must be allowlisted (not "*", which is unsafe
    # with credentials). Override via SAGEWAI_ADMIN_ALLOWED_ORIGINS (e.g. e2e on
    # 3808, or a production admin origin).
    _allowed_origins = [
        o.strip() for o in os.environ.get(
            "SAGEWAI_ADMIN_ALLOWED_ORIGINS",
            "http://localhost:3008,http://127.0.0.1:3008",
        ).split(",") if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # W6 hardening: security headers + request-size cap (multi-tenant only; the
    # outermost middleware so headers apply to every response, incl. errors).
    from sagewai.admin.hardening import SecurityHeadersMiddleware

    app.add_middleware(SecurityHeadersMiddleware)

    from sagewai.admin.auth_middleware import csrf_token_for, _LoginThrottle, _session_token_id
    from sagewai.admin.audit import emit_audit

    _login_throttle = _LoginThrottle()

    def _set_session_cookies(resp: JSONResponse, raw: str) -> None:
        secure = os.environ.get("SAGEWAI_ADMIN_TLS", "") in {"1", "true"}
        max_age = int(os.environ.get("SAGEWAI_ADMIN_SESSION_TTL_SECONDS", str(7 * 24 * 3600)))
        resp.set_cookie("sagewai_auth", raw, httponly=True, samesite="lax",
                        secure=secure, max_age=max_age, path="/")
        token_id = _session_token_id(raw)
        resp.set_cookie("sagewai_csrf", csrf_token_for(sf, token_id), httponly=False,
                        samesite="lax", secure=secure, max_age=max_age, path="/")

    async def _tenant_login_org(body: dict[str, Any]) -> dict[str, Any] | None:
        if identity_store is None:
            return None
        slug = body.get("org_slug") or body.get("org") or sf.get_org().get("org_slug")
        if slug:
            org = await identity_store.get_org_by_slug(slug)
            if org is not None:
                return org
        orgs = await identity_store.list_orgs()
        return orgs[0] if len(orgs) == 1 else None

    async def _tenant_session_result(
        org_id: str,
        user_id: str,
        *,
        raw: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        assert identity_store is not None
        raw = raw or await identity_store.issue_session(org_id, user_id)
        ctx = await identity_store.build_context(org_id, user_id, project_id=project_id)
        user = await identity_store.get_user(org_id, user_id)
        if user is None:
            raise RuntimeError("identity session resolved an unknown user")
        memberships = await identity_store.list_memberships(org_id, user_id)
        return {
            "access_token": raw,
            "token_type": "bearer",
            "user": _tenant_user_payload(user, ctx, memberships),
        }

    state = AdminState()
    # Use the durable SQLite-backed analytics store by default (Postgres when
    # SAGEWAI_DATABASE_URL is set). Falls back to the in-memory AnalyticsStore
    # is no longer the default — durability is zero-config now.
    analytics = PostgresAnalyticsStore(engine=_db_factory.get_engine())
    _register_optional_backends(sf)

    # Override /admin/agents and /admin/runs BEFORE include_router so
    # these direct app routes match first (Starlette matches routes in
    # registration order — whichever is added first wins). They merge
    # registry/playground agents and read agent runs from the file store
    # so the admin UI sees everything, not just SDK-registered state.
    @app.get("/admin/agents", include_in_schema=False)
    async def admin_agents_merged(request: Request) -> JSONResponse:
        """List every visible agent — playground specs from the file store."""
        pid = _project_scope(request)
        playground_agents = sf.list_agents(project_id=pid)
        # Count runs per agent from the file store
        runs_by_agent: dict[str, int] = {}
        for r in sf.list_agent_runs(project_id=pid, limit=1000, offset=0):
            name = r.get("agent_name", "")
            if name:
                runs_by_agent[name] = runs_by_agent.get(name, 0) + 1
        result = [
            {
                "name": a.get("name", ""),
                "capabilities": a.get("capabilities", []),
                "model": a.get("model", ""),
                "source": "playground",
                "strategy": a.get("strategy", ""),
                "tags": a.get("tags", []),
                "status": "active",
                "total_runs": runs_by_agent.get(a.get("name", ""), 0),
            }
            for a in playground_agents
        ]
        return JSONResponse(result)

    def _iso_to_epoch(value: Any) -> float | None:
        """Convert an ISO 8601 string to epoch seconds for the admin UI."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    @app.get("/admin/runs", include_in_schema=False)
    async def admin_runs_merged(
        request: Request,
        agent_name: str | None = None,
        status: str | None = None,
        run_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> JSONResponse:
        """List agent runs from the file store (standalone + workflow steps)."""
        store = _run_store(request)
        if store is not None:
            ctx = request.state.context
            records = await store.list_runs_for(
                ctx,
                agent_name=agent_name,
                status=status,
                run_type=run_type,
                limit=limit + 1,
                offset=offset,
            )
            runs = [r.to_dict() for r in records]
        else:
            pid = _project_scope(request)
            runs = sf.list_agent_runs(
                project_id=pid,
                agent_name=agent_name,
                status=status,
                run_type=run_type,
                limit=limit + 1,
                offset=offset,
            )
        items_raw = runs[:limit]
        has_more = len(runs) > limit
        items = [
            {
                "run_id": r.get("run_id", ""),
                "agent_name": r.get("agent_name", ""),
                "status": r.get("status", ""),
                "input_preview": (r.get("input_text") or "")[:100],
                "output_preview": (r.get("output_text") or "")[:100],
                "started_at": _iso_to_epoch(r.get("started_at")),
                "completed_at": _iso_to_epoch(r.get("completed_at")),
                "total_tokens": r.get("total_tokens", 0),
                "run_type": r.get("run_type", "standalone"),
                "parent_workflow_run_id": r.get("parent_workflow_run_id"),
            }
            for r in items_raw
        ]
        return JSONResponse({
            "items": items,
            "next_cursor": None,
            "has_more": has_more,
        })

    @app.get("/admin/runs/{run_id}", include_in_schema=False)
    async def admin_run_detail(run_id: str, request: Request) -> JSONResponse:
        """Return full detail for a single agent run from the file store."""
        store = _run_store(request)
        if store is not None:
            rec = await store.get_run_for(run_id, request.state.context)
            if rec is None:
                return JSONResponse(
                    {"detail": f"Run '{run_id}' not found"}, status_code=404
                )
            r = rec.to_dict()
        else:
            r = sf.get_agent_run(run_id)
            if r is None:
                return JSONResponse(
                    {"detail": f"Run '{run_id}' not found"}, status_code=404
                )
        return JSONResponse({
            "run_id": r.get("run_id", ""),
            "agent_name": r.get("agent_name", ""),
            "status": r.get("status", ""),
            "input_text": r.get("input_text", ""),
            "output_text": r.get("output_text", ""),
            "started_at": r.get("started_at"),
            "completed_at": r.get("completed_at"),
            "total_tokens": r.get("total_tokens", 0),
            "tool_calls": r.get("tool_calls", []),
            "steps": [],
            "run_type": r.get("run_type", "standalone"),
            "parent_workflow_run_id": r.get("parent_workflow_run_id"),
        })

    # Existing routers (note: the /admin/agents and /admin/runs routes
    # added above shadow the router's defaults thanks to registration
    # order — Starlette matches on first hit)
    app.include_router(create_admin_router(state), prefix="/admin")
    app.include_router(
        create_analytics_router(analytics), prefix="/api/v1/analytics"
    )
    app.include_router(
        create_analytics_router(analytics), prefix="/analytics"
    )

    # Autopilot routes (Plan 7)
    from sagewai.admin.autopilot_routes import create_autopilot_router

    app.include_router(
        create_autopilot_router(
            sf,
            provider_store_getter=lambda: getattr(app.state.resource_stores, "provider", None),
        ),
        prefix="/api/v1",
    )

    # Sandbox config routes (Plan 3b-i)
    from sagewai.admin import sandbox_routes

    sandbox_routes.register(app, sf)

    # Sealed environment routes (Sealed-i)
    from sagewai.admin import sealed_routes  # noqa: E402

    sealed_routes.register(app, sf)

    # Plan ART — artifact destination admin routes
    from sagewai.admin import artifact_destination_routes  # noqa: E402

    artifact_destination_routes.register(app)

    # Connections Platform PR4: generic CRUD admin routes delegating
    # to protocol plugins. Replaces the legacy connections_routes
    # (per-kind inference + tools) and oauth_routes (per-provider OAuth)
    # in one unified surface at /api/v1/admin/connections/*.
    from sagewai.admin import connections_v2_routes  # noqa: E402

    connections_v2_routes.register(app, sf)

    @app.api_route(
        "/api/v1/admin/inference-providers{rest:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        include_in_schema=False,
    )
    async def _inference_providers_redirect(rest: str) -> RedirectResponse:
        """Permanently moved to /api/v1/admin/connections; kept for one release."""
        return RedirectResponse(url=f"/api/v1/admin/connections{rest}", status_code=308)

    # Sealed-v directive admin routes (in-memory; postgres-backed
    # approvals/evaluations are wired alongside revocation_routes below
    # when a database URL is configured)
    from sagewai.admin import directive_routes  # noqa: E402

    directive_routes.register(app, sf)

    # Sealed revocation routes (Sealed-iii.A) — requires Postgres
    _db_url = _resolve_database_url()
    if _db_url:
        from sagewai.admin import revocation_routes  # noqa: E402
        from sagewai.core.stores.postgres import PostgresStore as _PostgresStore

        _revocation_store = _PostgresStore(database_url=_db_url)

        @app.on_event("startup")
        async def _init_revocation_store() -> None:  # type: ignore[misc]
            await _revocation_store.initialize()
            revocation_routes.register(app, _revocation_store)

        # Sealed-iii.C replay routes share the same Postgres store + the
        # admin app's workflow_registry. Workflow registry is populated by
        # the platform owner via app.state.workflow_registry; if absent,
        # routes return 404 for replay attempts (preview/commit) but still
        # register cleanly so the listing endpoint is callable.
        from sagewai.admin import replay_routes  # noqa: E402

        @app.on_event("startup")
        async def _init_replay_routes() -> None:  # type: ignore[misc]
            registry = getattr(app.state, "workflow_registry", {}) or {}
            replay_routes.register(app, _revocation_store, registry)

    # Harness admin routes (LLM proxy: policies, keys, spend, audit, config).
    # Single-org/dev without a DB keeps the lightweight in-memory store. Any
    # DB-backed or multi-tenant app uses the durable SQLAlchemy store; tenant
    # deployments must not lose proxy policy/key/spend state on process restart.
    from sagewai.harness import (
        HarnessConfig,
        InMemoryHarnessStore,
        RequestClassifier,
    )
    from sagewai.harness.admin_api import create_harness_admin_router

    if _db_url or _is_multi_tenant():
        from sagewai.harness.postgres_store import PostgresHarnessStore

        _identity_engine = getattr(identity_store, "_engine", None)
        _harness_store = (
            PostgresHarnessStore(database_url=_db_url)
            if _db_url
            else PostgresHarnessStore(engine=_identity_engine)
            if _identity_engine is not None
            else PostgresHarnessStore()
        )

        @app.on_event("startup")
        async def _init_harness_store() -> None:  # type: ignore[misc]
            await _harness_store.init()

    else:
        _harness_store = InMemoryHarnessStore()
    _harness_classifier = RequestClassifier()
    _harness_config = HarnessConfig()
    app.state.harness_store = _harness_store
    app.state.harness_config = _harness_config
    app.include_router(
        create_harness_admin_router(
            store=_harness_store,
            classifier=_harness_classifier,
            config=_harness_config,
        ),
        prefix="/api/v1/harness",
    )

    # ── Setup ────────────────────────────────────────────────────

    @app.get("/api/v1/setup/status")
    async def setup_status() -> JSONResponse:
        if sf.is_setup_complete():
            return JSONResponse({"setup_required": False})
        return JSONResponse({
            "setup_required": True,
            "reason": "No administrator account has been created yet.",
        })

    @app.post("/api/v1/setup")
    async def run_setup(request: Request) -> JSONResponse:
        body = await request.json()
        required = ["org_name", "admin_email", "admin_password"]
        missing = [f for f in required if not body.get(f)]
        if missing:
            return JSONResponse(
                {"ok": False, "message": f"Missing: {', '.join(missing)}"},
                status_code=422,
            )
        result = sf.complete_setup(**{
            k: body[k] for k in [
                "org_name", "org_slug", "contact_email", "timezone",
                "app_name", "app_description", "admin_name",
                "admin_email", "admin_password",
            ] if k in body
        })
        if not result.get("ok"):
            return JSONResponse(result, status_code=409)
        if _is_multi_tenant() and identity_store is not None:
            await identity_store.init()
            org_slug = result.get("org_slug") or _slugify(body["org_name"])
            org = await identity_store.get_org_by_slug(org_slug)
            if org is None:
                org = await identity_store.bootstrap_org(
                    body["org_name"],
                    org_slug,
                    contact_email=body.get("contact_email") or body.get("admin_email"),
                    tz=body.get("timezone", "UTC"),
                )
            app_slug = result.get("app_slug") or "default"
            if await identity_store.get_project_by_slug(org["id"], app_slug) is None:
                await identity_store.create_project(
                    org["id"],
                    app_slug,
                    body.get("app_name") or "Default",
                    environment="production",
                )
            if await identity_store.get_user_by_email(org["id"], body["admin_email"]) is None:
                await identity_store.create_user(
                    org["id"],
                    body["admin_email"],
                    password=body["admin_password"],
                    name=body.get("admin_name"),
                    role="org:owner",
                )
        logger.info("Setup completed for org=%s", result.get("org_slug"),
                     extra={"event": "setup.completed", "org_slug": result.get("org_slug", "")})
        otel_count("setup.completions")
        return JSONResponse(result)

    # ── Auth ─────────────────────────────────────────────────────

    @app.post("/api/v1/auth/login")
    async def auth_login(request: Request) -> JSONResponse:
        body = await request.json()
        email = body.get("email", "")
        ip = request.client.host if request.client else "?"
        key = f"{ip}:{email}"
        if _login_throttle.blocked(key):
            otel_count("auth.logins", status="throttled")
            return JSONResponse({"detail": "Too many attempts. Try again later."},
                                status_code=429, headers={"Retry-After": "900"})
        if _is_multi_tenant():
            org = await _tenant_login_org(body)
            user = (
                await identity_store.verify_credentials(org["id"], email, body.get("password", ""))
                if org is not None and identity_store is not None
                else None
            )
            if not user:
                _login_throttle.record_failure(key)
                otel_count("auth.logins", status="failed")
                return JSONResponse({"detail": "Invalid email or password"}, status_code=401)
            _login_throttle.reset(key)
            try:
                result = await _tenant_session_result(
                    org["id"], user["id"], project_id=body.get("project_id")
                )
            except Exception:
                logger.exception("Tenant login failed while building session context")
                return JSONResponse({"detail": "Invalid tenant context"}, status_code=401)
            logger.info("Login success for email=%s", result["user"]["email"],
                        extra={"event": "auth.login.success", "email": result["user"]["email"]})
            otel_count("auth.logins", status="success")
            resp = JSONResponse(jsonable_encoder(result))
            _set_session_cookies(resp, result["access_token"])
            return resp
        result = sf.validate_login(email, body.get("password", ""))
        if not result:
            _login_throttle.record_failure(key)
            logger.warning("Login failed for email=%s", email,
                           extra={"event": "auth.login.failed", "email": email})
            otel_count("auth.logins", status="failed")
            emit_audit(sf, event_type="auth.login.failed", actor_label=email)
            return JSONResponse({"detail": "Invalid email or password"}, status_code=401)
        _login_throttle.reset(key)
        logger.info("Login success for email=%s", result["user"]["email"],
                    extra={"event": "auth.login.success", "email": result["user"]["email"]})
        otel_count("auth.logins", status="success")
        emit_audit(sf, event_type="auth.login", actor_label=email)
        resp = JSONResponse(result)
        _set_session_cookies(resp, result["access_token"])
        return resp

    @app.post("/api/v1/auth/refresh")
    async def auth_refresh(request: Request) -> JSONResponse:
        cookie = request.cookies.get("sagewai_auth")
        if _is_multi_tenant():
            if not cookie or identity_store is None:
                return JSONResponse({"detail": "No session"}, status_code=401)
            sess = await identity_store.resolve_session(cookie)
            if sess is None:
                return JSONResponse({"detail": "No session"}, status_code=401)
            result = await _tenant_session_result(sess["org_id"], sess["user_id"])
            resp = JSONResponse(jsonable_encoder(result))
            _set_session_cookies(resp, result["access_token"])
            return resp
        result = sf.refresh_token(cookie) if cookie else None
        if not result:
            # Dev-mode bootstrap: when running locally with
            # SAGEWAI_DEV_TRUST_LOCAL=1 (set by the just autopilot-demo
            # recipe) and an admin user is provisioned, mint a fresh
            # session for the localhost browser. This removes the
            # manual login step from the local-dev demo flow. The env
            # var is never set in production, and the host check rejects
            # non-localhost callers, so this is not a prod backdoor.
            client_host = request.client.host if request.client else ""
            dev_trust = os.environ.get("SAGEWAI_DEV_TRUST_LOCAL", "").lower() in {"1", "true"}
            if dev_trust and client_host in {"127.0.0.1", "::1", "localhost"}:
                result = sf.issue_session()
        if not result:
            return JSONResponse({"detail": "No session"}, status_code=401)
        resp = JSONResponse(result)
        _set_session_cookies(resp, result["access_token"])
        return resp

    @app.post("/api/v1/auth/logout")
    async def auth_logout(request: Request) -> JSONResponse:
        raw = _extract_token(request)
        if _is_multi_tenant():
            actor = "unknown"
            if raw and identity_store is not None:
                sess = await identity_store.resolve_session(raw)
                if sess is not None:
                    user = await identity_store.get_user(sess["org_id"], sess["user_id"])
                    actor = (user or {}).get("email") or sess["user_id"]
                    await identity_store.revoke_session(raw)
            resp = JSONResponse({"status": "ok"})
            resp.delete_cookie("sagewai_auth", path="/")
            resp.delete_cookie("sagewai_csrf", path="/")
            logger.info("Tenant logout for actor=%s", actor,
                        extra={"event": "auth.logout", "actor": actor})
            return resp
        user = sf.get_user_by_token(raw) if raw else None
        actor = user.get("email") if user else "unknown"
        if raw:
            sf.revoke_session_token(raw)
        emit_audit(sf, event_type="auth.logout", actor_label=actor)
        resp = JSONResponse({"status": "ok"})
        resp.delete_cookie("sagewai_auth", path="/")
        resp.delete_cookie("sagewai_csrf", path="/")
        return resp

    @app.get("/api/v1/auth/me")
    async def auth_me(request: Request) -> JSONResponse:
        # AuthMiddleware already authenticated this (non-public) route and set
        # request.state.principal — resolve the user from the store, not by a
        # session-only token lookup, so API-token callers are treated the same.
        if getattr(request.state, "principal", None) is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        if _is_multi_tenant():
            ctx = request.state.context
            user = await identity_store.get_user(ctx.org_id, ctx.actor.id) if identity_store else None
            if not user:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            memberships = await identity_store.list_memberships(ctx.org_id, ctx.actor.id)
            return JSONResponse(
                jsonable_encoder(_tenant_user_payload(user, ctx, memberships))
            )
        user = sf.get_admin_user()
        if not user:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return JSONResponse(user)

    # ── Organization ─────────────────────────────────────────────

    @app.get("/api/v1/organization")
    async def get_org(request: Request) -> JSONResponse:
        if _is_multi_tenant():
            ctx = request.state.context
            org = await identity_store.get_org(ctx.org_id) if identity_store else None
            if org is None:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return JSONResponse(jsonable_encoder(_tenant_org_payload(org)))
        return JSONResponse(sf.get_org())

    @app.patch("/api/v1/organization")
    async def update_org(request: Request) -> JSONResponse:
        require_org_admin(request.state.context)
        body = await request.json()
        if _is_multi_tenant():
            ctx = request.state.context
            current = await identity_store.get_org(ctx.org_id) if identity_store else None
            if current is None:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            settings = dict(current.get("settings") or {})
            for key in ("app_url", "industry", "team_size"):
                if key in body:
                    settings[key] = body[key]
            patch: dict[str, Any] = {}
            if "org_name" in body or "name" in body:
                patch["name"] = body.get("org_name", body.get("name"))
            if "contact_email" in body:
                patch["contact_email"] = body["contact_email"]
            if "timezone" in body:
                patch["timezone"] = body["timezone"]
            if settings != (current.get("settings") or {}):
                patch["settings"] = settings
            result = await identity_store.update_org(ctx.org_id, patch) if identity_store else None
            if result is None:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return JSONResponse(jsonable_encoder(_tenant_org_payload(result)))
        result = sf.update_org(body)
        emit_audit(sf, event_type="org.updated",
                   actor_label=request.state.principal.actor_label,
                   details={"patched_keys": list(body.keys())})
        return JSONResponse(result)

    # ── Projects ─────────────────────────────────────────────────

    @app.get("/api/v1/projects")
    async def list_projects(request: Request) -> JSONResponse:
        if _is_multi_tenant():
            projects = await identity_store.list_projects(request.state.context.org_id)
            return JSONResponse(jsonable_encoder([_tenant_project_payload(p) for p in projects]))
        return JSONResponse(sf.list_projects())

    @app.post("/api/v1/projects")
    async def create_project(request: Request) -> JSONResponse:
        require_org_admin(request.state.context)
        body = await request.json()
        if _is_multi_tenant():
            from sqlalchemy.exc import IntegrityError

            ctx = request.state.context
            name = body.get("name", "")
            slug = body.get("slug") or _slugify(name)
            settings = {
                "allowed_origins": body.get("allowed_origins", ""),
                "default_model": body.get("default_model"),
            }
            try:
                project = await identity_store.create_project(
                    ctx.org_id,
                    slug,
                    name,
                    environment=body.get("environment", "production"),
                    settings=settings,
                )
                return JSONResponse(
                    jsonable_encoder(_tenant_project_payload(project)),
                    status_code=201,
                )
            except IntegrityError:
                return JSONResponse({"detail": f"Project '{slug}' already exists."}, status_code=409)
        try:
            project = sf.create_project(
                name=body.get("name", ""),
                slug=body.get("slug", ""),
                environment=body.get("environment", "production"),
                allowed_origins=body.get("allowed_origins", ""),
            )
            return JSONResponse(project, status_code=201)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/v1/projects/{slug}")
    async def get_project(slug: str, request: Request) -> JSONResponse:
        if _is_multi_tenant():
            project = await identity_store.get_project_by_slug(request.state.context.org_id, slug)
            if project is None:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return JSONResponse(jsonable_encoder(_tenant_project_payload(project)))
        for p in sf.list_projects():
            if p["slug"] == slug:
                return JSONResponse(p)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.patch("/api/v1/projects/{slug}")
    async def update_project(slug: str, request: Request) -> JSONResponse:
        require_org_admin(request.state.context)
        body = await request.json()
        if _is_multi_tenant():
            ctx = request.state.context
            project = await identity_store.get_project_by_slug(ctx.org_id, slug)
            if project is None:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            settings_patch = {
                k: body[k]
                for k in ("allowed_origins", "default_model")
                if k in body
            }
            result = await identity_store.update_project(
                ctx.org_id,
                project["id"],
                name=body.get("name"),
                environment=body.get("environment"),
                status=body.get("status"),
                settings_patch=settings_patch,
            )
            if result is None:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return JSONResponse(jsonable_encoder(_tenant_project_payload(result)))
        result = sf.update_project(slug, body)
        if result is None:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return JSONResponse(result)

    @app.delete("/api/v1/projects/{slug}")
    async def delete_project(slug: str, request: Request) -> JSONResponse:
        require_org_admin(request.state.context)
        if _is_multi_tenant():
            return JSONResponse(
                {
                    "detail": (
                        "Project deletion is not supported in multi-tenant mode; "
                        "set status='inactive' instead."
                    )
                },
                status_code=409,
            )
        try:
            if sf.delete_project(slug):
                return JSONResponse({"status": "ok"})
            return JSONResponse({"detail": "Not found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)

    # ── Providers ────────────────────────────────────────────────

    @app.get("/api/v1/providers")
    async def list_providers(request: Request) -> JSONResponse:
        store = _provider_store(request)
        if store is not None:
            return JSONResponse(await store.list(ctx=request.state.context))
        pid = _project_scope(request)
        return JSONResponse(sf.list_providers(project_id=pid))

    @app.post("/api/v1/providers")
    async def upsert_provider(request: Request) -> JSONResponse:
        body = await request.json()
        _require_resource_write(request)
        store = _provider_store(request)
        if store is not None:
            result = await store.upsert(body, ctx=request.state.context)
            await _emit_audit(request, "provider.upsert", target_type="provider", target_id=result.get("id", ""))
            return JSONResponse({"id": result.get("id", "")})
        pid = _project_scope(request)
        if _multi_ctx(request) is not None:
            # Scope from the session, never the request body (no scope injection).
            body["project_id"] = _owner(pid)
        elif pid:
            body["project_id"] = pid
        result = sf.upsert_provider(body)
        await _emit_audit(request, "provider.upsert", target_type="provider", target_id=result.get("id", ""))
        logger.info("Provider configured: %s", body.get("provider_name", ""),
                     extra={"event": "provider.configured", "provider": body.get("provider_name", "")})
        return JSONResponse({"id": result.get("id", "")})

    @app.post("/api/v1/providers/{provider_id}/test")
    async def test_provider(provider_id: str, request: Request) -> JSONResponse:
        from sagewai.admin.provider_probes import test_cloud_provider

        provider = await _provider_decrypted(request, provider_id)
        if not provider:
            return JSONResponse({"detail": "Provider not found"}, status_code=404)
        result = await test_cloud_provider(
            provider.get("provider_name", ""),
            provider.get("config", {}),
        )
        status = "success" if result.get("connected") else "failed"
        logger.info("Provider test %s: %s latency=%.0fms",
                     status, provider.get("provider_name", ""), result.get("latency_ms", 0),
                     extra={"event": f"provider.test.{status}",
                            "provider": provider.get("provider_name", ""),
                            "latency_ms": result.get("latency_ms", 0)})
        otel_count("provider.tests", provider=provider.get("provider_name", ""), status=status)
        if result.get("latency_ms"):
            otel_record("provider.test.latency", result["latency_ms"],
                        provider=provider.get("provider_name", ""))
        return JSONResponse(result)

    @app.delete("/api/v1/providers/{provider_id}")
    async def delete_provider(provider_id: str, request: Request) -> JSONResponse:
        store = _provider_store(request)
        if store is not None:
            _require_resource_write(request)
            if await store.delete(provider_id, ctx=request.state.context):
                await _emit_audit(request, "provider.delete", target_type="provider", target_id=provider_id)
                return JSONResponse({"status": "ok"})
            return JSONResponse({"detail": "Not found"}, status_code=404)
        ctx = _multi_ctx(request)
        if ctx is not None:
            # Multi-tenant: only a provider in the actor's WRITE scope may be
            # deleted (own project; org-shared needs org-admin). A provider in
            # another project is invisible -> 404 (no existence leak).
            prov = sf.get_provider_decrypted(provider_id, project_id=ctx.project_id)
            ppid = (prov or {}).get("project_id")
            writable = (ppid == ctx.project_id) if ctx.project_id is not None else (ppid in (None, ""))
            if not prov or not writable:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            _require_resource_write(request)
        if sf.delete_provider(provider_id):
            await _emit_audit(request, "provider.delete", target_type="provider", target_id=provider_id)
            return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.post("/api/v1/providers/{provider_id}/default")
    async def set_default_provider(provider_id: str, request: Request) -> JSONResponse:
        """Mark *provider_id* as the default LLM provider for the project scope.

        At most one provider can be the default per project (or org-global
        when no ``X-Project-ID`` header is present). The autopilot mission
        driver will pick this provider's credentials before any other.
        """
        _require_resource_write(request)
        store = _provider_store(request)
        if store is not None:
            result = await store.set_default(provider_id, ctx=request.state.context)
            if result is None:
                return JSONResponse({"detail": "Provider not found"}, status_code=404)
            await _emit_audit(request, "provider.set_default", target_type="provider", target_id=provider_id)
            return JSONResponse({"status": "ok", "id": provider_id, "default": True})
        pid = _project_scope(request)
        result = sf.set_default_provider(provider_id, project_id=pid)
        if result is None:
            return JSONResponse({"detail": "Provider not found"}, status_code=404)
        logger.info(
            "Provider set default: %s (project=%s)",
            result.get("provider_name", ""),
            pid,
            extra={"event": "provider.default", "provider": result.get("provider_name", "")},
        )
        await _emit_audit(request, "provider.set_default", target_type="provider", target_id=provider_id)
        return JSONResponse({"status": "ok", "id": result.get("id"), "default": True})

    @app.get("/api/v1/providers/ollama/models")
    async def ollama_models() -> JSONResponse:
        from sagewai.admin.provider_probes import detect_ollama
        return JSONResponse(await detect_ollama())

    @app.get("/api/v1/providers/lmstudio/models")
    async def lmstudio_models() -> JSONResponse:
        from sagewai.admin.provider_probes import detect_lmstudio
        return JSONResponse(await detect_lmstudio())

    # ── Playground metadata ──────────────────────────────────────

    @app.get("/playground/models")
    async def playground_models(request: Request) -> JSONResponse:
        from sagewai.admin.provider_probes import aggregate_available_models
        providers = await _providers_decrypted(request)
        models = await aggregate_available_models(providers)
        return JSONResponse(models)

    @app.get("/playground/strategies")
    async def playground_strategies() -> JSONResponse:
        return JSONResponse([s["id"] for s in _STRATEGIES])

    @app.get("/playground/capabilities")
    async def playground_capabilities(request: Request) -> JSONResponse:
        """CapabilityCatalog with MCP servers sourced from registered connections.

        MCP servers are derived from the project's connections at request
        time (project-aware via ``X-Project-ID``), replacing the legacy
        hardcoded fixture list. Other capability buckets (tools, memory,
        guardrails, strategies) stay constant for now.
        """
        from sagewai.connections.bootstrap import build_connections_context
        from sagewai.connections.store_ops import store_list

        pid = _project_scope(request)
        connection_store = getattr(
            getattr(getattr(request.app, "state", None), "resource_stores", None),
            "connection",
            None,
        )
        try:
            if _multi_ctx(request) is not None:
                if connection_store is None:
                    mcp_connections = []
                else:
                    mctx = _multi_ctx(request)
                    org_connections = list(
                        await store_list(connection_store, None, protocol="mcp")
                    )
                    if mctx.project_id is None:
                        mcp_connections = org_connections
                    else:
                        project_connections = list(
                            await store_list(
                                connection_store, mctx.project_id, protocol="mcp"
                            )
                        )
                        seen = set()
                        mcp_connections = []
                        for conn in project_connections + org_connections:
                            key = (conn.protocol, conn.display_name)
                            if key in seen:
                                continue
                            seen.add(key)
                            mcp_connections.append(conn)
            else:
                ctx = build_connections_context(sf)
                mcp_connections = ctx.store.list(pid, protocol="mcp")
        except Exception:
            mcp_connections = []
        body = dict(_CAPABILITIES)
        body["mcp_servers"] = [
            {
                "id": c.id,
                "name": c.display_name,
                "description": (
                    f"{c.protocol_data.get('server_ref') or 'custom'} via "
                    f"{c.protocol_data.get('transport', '?')} — "
                    f"{len(c.protocol_data.get('discovered_tools', []))} tools"
                ),
            }
            for c in mcp_connections
        ]
        return JSONResponse(body)

    @app.get("/playground/presets")
    async def playground_presets() -> JSONResponse:
        return JSONResponse(_PRESETS)

    @app.post("/playground/agent")
    async def create_playground_agent(request: Request) -> JSONResponse:
        """Create or update a playground agent from a spec."""
        body = await request.json()
        if not body.get("name"):
            return JSONResponse({"detail": "Agent name is required"}, status_code=422)
        _require_resource_write(request)
        store = _agent_store(request)
        if store is not None:
            agent = await store.create(body, ctx=request.state.context)
            await _emit_audit(request, "agent.create", target_type="agent", target_id=body.get("name", ""))
            return JSONResponse(agent, status_code=201)
        pid = _project_scope(request)
        agent = sf.create_agent(body, project_id=_owner(pid))
        await _emit_audit(request, "agent.create", target_type="agent", target_id=body.get("name", ""))
        logger.info("Agent created: %s model=%s strategy=%s",
                     body["name"], body.get("model", ""), body.get("strategy", ""),
                     extra={"event": "agent.created", "agent_name": body["name"],
                            "model": body.get("model", ""), "strategy": body.get("strategy", "")})
        otel_count("agent.created", agent_name=body["name"])
        return JSONResponse(agent, status_code=201)

    @app.get("/playground/agents")
    async def playground_agents(request: Request) -> JSONResponse:
        store = _agent_store(request)
        if store is not None:
            return JSONResponse(await store.list(ctx=request.state.context))
        pid = _project_scope(request)
        agents = sf.list_agents(project_id=pid)
        return JSONResponse(agents)

    @app.get("/playground/agents/{name}")
    async def playground_agent_detail(name: str, request: Request) -> JSONResponse:
        store = _agent_store(request)
        if store is not None:
            agent = await store.get(name, ctx=request.state.context)
            if not agent:
                return JSONResponse({"detail": "Agent not found"}, status_code=404)
            return JSONResponse(agent)
        pid = _project_scope(request)
        agent = sf.get_agent(name, project_id=pid)
        if not agent:
            return JSONResponse({"detail": "Agent not found"}, status_code=404)
        return JSONResponse(agent)

    @app.get("/playground/agents/{name}/debug")
    async def playground_agent_debug(name: str, request: Request) -> JSONResponse:
        agent = await _resolve_agent(request, name)
        if not agent:
            return JSONResponse({"detail": "Agent not found"}, status_code=404)
        return JSONResponse(agent)

    @app.delete("/playground/agents/{name}")
    async def delete_playground_agent(name: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        store = _agent_store(request)
        if store is not None:
            if await store.delete(name, ctx=request.state.context):
                await _emit_audit(request, "agent.delete", target_type="agent", target_id=name)
                return JSONResponse({"status": "ok"})
            return JSONResponse({"detail": "Not found"}, status_code=404)
        pid = _project_scope(request)
        if sf.delete_agent(name, project_id=pid):
            await _emit_audit(request, "agent.delete", target_type="agent", target_id=name)
            return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.post("/playground/agents/{name}/rename")
    async def rename_playground_agent(name: str, request: Request) -> JSONResponse:
        body = await request.json()
        new_name = body.get("new_name", "")
        if not new_name:
            return JSONResponse({"detail": "new_name required"}, status_code=422)
        store = _agent_store(request)
        if store is not None:
            _require_resource_write(request)
            result = await store.rename(name, new_name, ctx=request.state.context)
            if not result:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            await _emit_audit(request, "agent.rename", target_type="agent", target_id=new_name)
            return JSONResponse(result)
        pid = _project_scope(request)
        result = sf.rename_agent(name, new_name, project_id=pid)
        if result:
            return JSONResponse(result)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.post("/playground/run")
    async def playground_run(request: Request):
        """Run an agent and stream the response as SSE."""

        import secrets as _run_sec
        import time as _run_time
        body = await request.json()
        # The admin UI sends either `agent_name` or `name`; accept both.
        agent_name = body.get("agent_name") or body.get("name") or ""
        message = body.get("message", "")
        pid = _project_scope(request)
        _enforce_run_quota(request)
        agent_spec = await _resolve_agent(request, agent_name)
        run_id = f"run-{_run_sec.token_hex(6)}"
        _run_t0 = _run_time.monotonic()
        _started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        logger.info("Agent run started: agent=%s model=%s",
                     agent_name, (agent_spec or {}).get("model", ""),
                     extra={"event": "agent.run.started", "agent_name": agent_name,
                            "model": (agent_spec or {}).get("model", "")})
        otel_count("agent.runs", agent_name=agent_name)

        async def _generate():
            model = (agent_spec or {}).get("model", "")
            system_prompt = (agent_spec or {}).get("system_prompt", "")
            full_output = ""
            status = "completed"

            yield f"event: run_started\ndata: {json.dumps({'run_id': run_id, 'agent': agent_name})}\n\n"

            try:
                import litellm
                litellm.suppress_debug_info = True

                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append({"role": "user", "content": message})

                completion_kwargs = await _litellm_completion_kwargs(request, model)
                model = completion_kwargs.get("model") or model
                response = await litellm.acompletion(
                    messages=messages,
                    stream=True,
                    **completion_kwargs,
                )

                async for chunk in response:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        full_output += delta
                        yield f"event: text_message_content\ndata: {json.dumps({'delta': delta})}\n\n"

                dt = _run_time.monotonic() - _run_t0
                logger.info("Agent run completed: agent=%s model=%s tokens=%d duration=%.1fs",
                             agent_name, model, len(full_output.split()), dt,
                             extra={"event": "agent.run.completed", "agent_name": agent_name,
                                    "model": model, "duration_s": round(dt, 2)})
                otel_record("agent.run.duration", dt, agent_name=agent_name, model=model)
                yield f"event: run_finished\ndata: {json.dumps({'output': full_output, 'status': 'completed', 'run_id': run_id})}\n\n"

            except ImportError:
                status = "failed"
                full_output = "litellm is not installed. Run: uv pip install litellm"
                logger.error("Agent run failed: litellm not installed",
                             extra={"event": "agent.run.error", "agent_name": agent_name, "error": full_output})
                otel_count("agent.run.errors", agent_name=agent_name, error="import")
                yield f"event: text_message_content\ndata: {json.dumps({'delta': full_output})}\n\n"
                yield f"event: run_finished\ndata: {json.dumps({'output': full_output, 'status': 'error', 'run_id': run_id})}\n\n"

            except Exception as exc:
                status = "failed"
                error_msg = str(exc)
                logger.error("Agent run failed: agent=%s error=%s", agent_name, error_msg[:200],
                             extra={"event": "agent.run.error", "agent_name": agent_name,
                                    "model": model, "error": error_msg[:200]})
                otel_count("agent.run.errors", agent_name=agent_name, error="runtime")
                if (
                    "api_key" in error_msg.lower()
                    or "authentication" in error_msg.lower()
                    or "no llm provider" in error_msg.lower()
                    or "project-scoped credentials" in error_msg.lower()
                ):
                    full_output = (
                        f"No API key configured for model '{model}'. "
                        f"Go to System → AI Models to add your API key, "
                        f"or configure a project-scoped provider."
                    )
                else:
                    full_output = f"Error running agent: {error_msg}"
                yield f"event: text_message_content\ndata: {json.dumps({'delta': full_output})}\n\n"
                yield f"event: run_finished\ndata: {json.dumps({'output': full_output, 'status': 'error', 'run_id': run_id})}\n\n"

            finally:
                # Persist the run record — success and failure alike.
                # Without this, /admin/runs is always empty for playground
                # traffic (no Postgres RunStore and the in-memory AdminState
                # isn't wired to this handler).
                try:
                    est_tokens = (len(message) + len(full_output)) // 4
                    store = _run_store(request)
                    if store is not None:
                        await store.save_run_for(
                            request.state.context,
                            run_id=run_id,
                            agent_name=agent_name,
                            status=status,
                            input_text=message,
                            output_text=full_output,
                            total_tokens=est_tokens,
                            started_at=_iso_to_epoch(_started_at),
                            completed_at=_run_time.time(),
                            run_type="standalone",
                            parent_workflow_run_id=None,
                            tool_calls=[],
                            model=model,
                        )
                    else:
                        sf.save_agent_run({
                            "run_id": run_id,
                            "agent_name": agent_name,
                            "model": model,
                            "status": status,
                            "input_text": message,
                            "output_text": full_output,
                            "total_tokens": est_tokens,
                            "started_at": _started_at,
                            "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "run_type": "standalone",
                            "parent_workflow_run_id": None,
                            "tool_calls": [],
                            "project_id": _owner(pid),
                        })
                except Exception as persist_exc:
                    logger.error("Failed to persist agent run %s: %s", run_id, persist_exc)

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Prompt logs ───────────────────────────────────────────────

    @app.get("/api/v1/prompts/logs")
    async def list_prompt_logs(request: Request) -> JSONResponse:
        pstore = _prompt_store(request)
        if pstore is not None:
            records = await pstore.list_prompt_logs_for(
                request.state.context,
                run_id=request.query_params.get("run_id"),
                agent_name=request.query_params.get("agent_name"),
                model=request.query_params.get("model"),
                limit=int(request.query_params.get("limit", "50")),
                offset=int(request.query_params.get("offset", "0")),
            )
            return JSONResponse([r.to_dict() for r in records])
        data = sf._read()
        logs = [
            log
            for log in data.get("prompt_logs", [])
            if _in_read_scope(log.get("project_id"), request)
        ]
        return JSONResponse(logs)

    @app.post("/api/v1/prompts/logs")
    async def save_prompt_log(request: Request) -> JSONResponse:
        body = await request.json()
        _require_resource_write(request)
        pstore = _prompt_store(request)
        if pstore is not None:
            # A log's project is stamped from ctx, but the body run_id is trusted.
            # Reject a run_id outside the caller's scope (cross-project/nonexistent)
            # so a PA log can never reference PB's run. Existence-hidden -> 404.
            rid = body.get("run_id") or ""
            if rid:
                rstore = _run_store(request)
                if (
                    rstore is not None
                    and await rstore.get_run_for(rid, request.state.context) is None
                ):
                    return JSONResponse({"detail": "run not found"}, status_code=404)
            log_id = await pstore.save_prompt_log_for(
                request.state.context,
                run_id=body.get("run_id", ""),
                agent_name=body.get("agent_name", ""),
                step_index=body.get("step_index", 0),
                model=body.get("model", ""),
                prompt_messages=body.get("prompt_messages"),
                response_message=body.get("response_message"),
                input_tokens=body.get("input_tokens", 0),
                output_tokens=body.get("output_tokens", 0),
                cost_usd=body.get("cost_usd", 0.0),
                duration_ms=body.get("duration_ms", 0),
                strategy=body.get("strategy", "react"),
                metadata=body.get("metadata"),
                is_example=body.get("is_example", False),
                tags=body.get("tags"),
                source=body.get("source", "playground"),
                input_text=body.get("input_text", ""),
                output_text=body.get("output_text", ""),
            )
            await _emit_audit(request, "prompt_log.create", target_type="prompt_log", target_id=log_id)
            return JSONResponse({"log_id": log_id}, status_code=201)
        pid = _project_scope(request)
        import secrets as _sec
        log_id = f"log-{_sec.token_hex(6)}"
        entry = {
            "log_id": log_id,
            "agent_name": body.get("agent_name", ""),
            "model": body.get("model", ""),
            "input_text": body.get("input_text", ""),
            "output_text": body.get("output_text", ""),
            "total_tokens": body.get("total_tokens", 0),
            "tags": body.get("tags", []),
            "source": body.get("source", "playground"),
            "is_example": body.get("is_example", False),
            "quality": body.get("quality", 0),
            "project_id": _owner(pid),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        def _apply(d):
            d.setdefault("prompt_logs", []).append(entry)
        sf.mutate(_apply)
        await _emit_audit(request, "prompt_log.create", target_type="prompt_log", target_id=log_id)
        return JSONResponse({"log_id": log_id}, status_code=201)

    @app.get("/api/v1/prompts/logs/{log_id}")
    async def get_prompt_log(log_id: str, request: Request) -> JSONResponse:
        pstore = _prompt_store(request)
        if pstore is not None:
            rec = await pstore.get_prompt_log_for(log_id, request.state.context)
            if rec is None:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return JSONResponse(rec.to_dict())
        data = sf._read()
        for log in data.get("prompt_logs", []):
            if log.get("log_id") == log_id:
                if not _in_read_scope(log.get("project_id"), request):
                    return JSONResponse({"detail": "Not found"}, status_code=404)
                return JSONResponse(log)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.patch("/api/v1/prompts/logs/{log_id}")
    async def update_prompt_log(log_id: str, request: Request) -> JSONResponse:
        body = await request.json()
        _require_resource_write(request)
        pstore = _prompt_store(request)
        if pstore is not None:
            rec = await pstore.update_prompt_log_for(
                log_id,
                request.state.context,
                tags=body.get("tags"),
                is_example=body.get("is_example"),
            )
            if rec is None:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            await _emit_audit(request, "prompt_log.update", target_type="prompt_log", target_id=log_id)
            return JSONResponse(rec.to_dict())
        def _apply(d):
            for log in d.get("prompt_logs", []):
                if log.get("log_id") == log_id and _in_write_scope(
                    log.get("project_id"), request
                ):
                    for k in ("tags", "is_example", "output_text"):
                        if k in body:
                            log[k] = body[k]
                    return log
            return None
        result = sf.mutate(_apply)
        if result is not None:
            await _emit_audit(request, "prompt_log.update", target_type="prompt_log", target_id=log_id)
            return JSONResponse(result)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/prompts/logs/{log_id}")
    async def delete_prompt_log(log_id: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        pstore = _prompt_store(request)
        if pstore is not None:
            ok = await pstore.delete_prompt_log_for(log_id, request.state.context)
            if not ok:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            await _emit_audit(request, "prompt_log.delete", target_type="prompt_log", target_id=log_id)
            return JSONResponse({"status": "ok"})
        state = {"hit": False}

        def _apply(d):
            kept = []
            for log in d.get("prompt_logs", []):
                if log.get("log_id") == log_id and _in_write_scope(
                    log.get("project_id"), request
                ):
                    state["hit"] = True
                    continue
                kept.append(log)
            d["prompt_logs"] = kept

        sf.mutate(_apply)
        if _multi_ctx(request) is not None and not state["hit"]:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if state["hit"]:
            await _emit_audit(request, "prompt_log.delete", target_type="prompt_log", target_id=log_id)
        return JSONResponse({"status": "ok"})

    @app.post("/api/v1/prompts/replay")
    async def replay_prompt(request: Request) -> JSONResponse:
        return JSONResponse({"detail": "Replay requires a running agent"}, status_code=501)

    @app.get("/api/v1/prompts/export")
    async def export_prompts(request: Request) -> JSONResponse:
        pstore = _prompt_store(request)
        if pstore is not None:
            records = await pstore.list_prompt_logs_for(
                request.state.context, limit=10000
            )
            return JSONResponse([r.to_dict() for r in records])
        pid = _project_scope(request)
        data = sf._read()
        logs = data.get("prompt_logs", [])
        if pid:
            logs = [l for l in logs if l.get("project_id") in (pid, None)]
        return JSONResponse(logs)

    @app.get("/api/v1/prompts/examples")
    async def list_prompt_examples(request: Request) -> JSONResponse:
        pstore = _prompt_store(request)
        if pstore is not None:
            records = await pstore.list_examples_for(
                request.state.context,
                agent_name=request.query_params.get("agent_name") or None,
                limit=int(request.query_params.get("limit", "50")),
            )
            return JSONResponse([r.to_dict() for r in records])
        pid = _project_scope(request)
        data = sf._read()
        examples = [l for l in data.get("prompt_logs", []) if l.get("is_example")]
        if pid:
            examples = [e for e in examples if e.get("project_id") in (pid, None)]
        return JSONResponse(examples)

    # ── Training data export (for Unsloth fine-tuning) ───────────

    @app.get("/api/v1/training/export")
    async def export_training_data(request: Request):
        """Export training samples as JSONL for fine-tuning with Unsloth.

        Query params:
          format: "alpaca" (default) | "sharegpt" | "raw"
          project_id: filter by project (also reads X-Project-ID header)
          min_quality: minimum quality rating (1-5, default 0)
          agent_name: filter by agent
        """
        from starlette.responses import Response

        pid = _project_scope(request) or request.query_params.get("project_id")
        fmt = request.query_params.get("format", "alpaca")
        min_quality = int(request.query_params.get("min_quality", "0"))
        agent_filter = request.query_params.get("agent_name")

        pstore = _prompt_store(request)
        if pstore is not None:
            records = await pstore.list_examples_for(request.state.context, limit=10000)
            samples = [r.to_dict() for r in records]
        else:
            data = sf._read()
            samples = [l for l in data.get("prompt_logs", []) if l.get("is_example")]
            # Filter by project (file-store path only; the PG path is ctx-scoped)
            if pid:
                samples = [s for s in samples if s.get("project_id") in (pid, None)]
        if min_quality > 0:
            samples = [s for s in samples if (s.get("quality", 0) or 0) >= min_quality]
        if agent_filter:
            samples = [s for s in samples if s.get("agent_name") == agent_filter]

        lines = []
        for s in samples:
            inp = s.get("input_text", "")
            out = s.get("output_text", "")
            if not inp or not out:
                continue

            if fmt == "sharegpt":
                # ShareGPT format — multi-turn conversations
                entry = {
                    "conversations": [
                        {"from": "human", "value": inp},
                        {"from": "gpt", "value": out},
                    ]
                }
            elif fmt == "raw":
                # Raw format — all fields
                entry = s
            else:
                # Alpaca format (default) — instruction/input/output
                system = ""
                agent = await _resolve_agent(request, s.get("agent_name", ""))
                if agent:
                    system = agent.get("system_prompt", "")
                entry = {
                    "instruction": system or "You are a helpful assistant.",
                    "input": inp,
                    "output": out,
                }

            lines.append(json.dumps(entry, ensure_ascii=False))

        content = "\n".join(lines)
        filename = f"training-data-{fmt}-{len(lines)}samples.jsonl"

        logger.info("Training data exported: %d samples, format=%s, project=%s",
                     len(lines), fmt, pid or "all",
                     extra={"event": "training.export", "samples": len(lines),
                            "format": fmt, "project_id": pid or "global"})

        return Response(
            content=content,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/v1/training/stats")
    async def training_stats(request: Request) -> JSONResponse:
        """Training data statistics for the current project."""
        pstore = _prompt_store(request)
        if pstore is not None:
            ctx = request.state.context
            example_records = await pstore.list_examples_for(ctx, limit=10000)
            examples = [r.to_dict() for r in example_records]
            all_log_records = await pstore.list_prompt_logs_for(ctx, limit=10000)
            total_logs = len(all_log_records)
        else:
            pid = _project_scope(request)
            data = sf._read()
            all_logs = data.get("prompt_logs", [])
            examples = [l for l in all_logs if l.get("is_example")]
            if pid:
                examples = [e for e in examples if e.get("project_id") in (pid, None)]
            total_logs = len(all_logs)

        # Stats by agent
        by_agent: dict[str, int] = {}
        for e in examples:
            agent = e.get("agent_name", "unknown")
            by_agent[agent] = by_agent.get(agent, 0) + 1

        return JSONResponse({
            "total_samples": len(examples),
            "total_logs": total_logs,
            "by_agent": by_agent,
            "formats_available": ["alpaca", "sharegpt", "raw"],
            "export_url": "/api/v1/training/export",
        })

    @app.post("/api/v1/training/samples/{log_id}/quality")
    async def rate_training_sample(log_id: str, request: Request) -> JSONResponse:
        """Rate a training sample quality (1-5)."""
        body = await request.json()
        quality = max(1, min(5, int(body.get("quality", 3))))
        pstore = _prompt_store(request)
        if pstore is not None:
            ctx = request.state.context
            if await pstore.get_prompt_log_for(log_id, ctx) is None:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            _require_resource_write(request)  # viewer -> 403
            await pstore.update_prompt_log_for(
                log_id, ctx, quality=quality, is_example=True
            )
            await _emit_audit(
                request,
                "prompt_log.quality",
                target_type="prompt_log",
                target_id=log_id,
            )
            return JSONResponse({"status": "ok", "quality": quality})
        def _apply(d):
            for log in d.get("prompt_logs", []):
                if log.get("log_id") == log_id:
                    log["quality"] = max(1, min(5, int(quality)))
                    log["is_example"] = True  # rating implies it's a training sample
                    return log["quality"]
            return None
        result = sf.mutate(_apply)
        if result is not None:
            return JSONResponse({"status": "ok", "quality": result})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.get("/strategies/list")
    async def strategies_list() -> JSONResponse:
        return JSONResponse(_STRATEGIES)

    @app.get("/strategies/detail")
    async def strategies_detail() -> JSONResponse:
        return JSONResponse(_STRATEGIES)

    # ── Model router ─────────────────────────────────────────────

    @app.get("/api/v1/model-router/models")
    async def model_router_models(request: Request) -> JSONResponse:
        from sagewai.admin.provider_probes import aggregate_available_models
        providers = await _providers_decrypted(request)
        models = await aggregate_available_models(providers)
        return JSONResponse(models)

    @app.get("/api/v1/model-router/rules")
    async def model_router_rules() -> JSONResponse:
        return JSONResponse([])

    @app.post("/api/v1/model-router/test")
    async def model_router_test(request: Request) -> JSONResponse:
        body = await request.json()
        # Simple complexity classification
        prompt = body.get("prompt", "")
        word_count = len(prompt.split())
        if word_count < 20:
            tier = "simple"
        elif word_count < 100:
            tier = "medium"
        else:
            tier = "complex"
        return JSONResponse({"tier": tier, "prompt_words": word_count})

    # ── Workflows ────────────────────────────────────────────────

    _WORKFLOW_TEMPLATES = [
        {
            "name": "Research → Write → Edit",
            "description": "Three-agent sequential pipeline: a Researcher gathers facts, a Writer drafts content, and an Editor polishes the output.",
            "yaml": (
                "name: Research Pipeline\n"
                "description: Researcher → Writer → Editor\n"
                "agents:\n"
                "  researcher:\n"
                "    model: gpt-4o\n"
                "    system_prompt: You are a thorough researcher. Gather facts and cite sources.\n"
                "  writer:\n"
                "    model: gpt-4o\n"
                "    system_prompt: You are a skilled writer. Draft clear, engaging content from research notes.\n"
                "  editor:\n"
                "    model: gpt-4o-mini\n"
                "    system_prompt: You are a precise editor. Polish grammar, clarity, and structure.\n"
                "workflow:\n"
                "  type: sequential\n"
                "  steps:\n"
                "    - agent: researcher\n"
                "    - agent: writer\n"
                "    - agent: editor\n"
            ),
            "agents": ["researcher", "writer", "editor"],
        },
        {
            "name": "Parallel Analysis",
            "description": "Run multiple analysis agents in parallel, then merge results with a synthesizer.",
            "yaml": (
                "name: Parallel Analysis\n"
                "description: Three analysts run concurrently, synthesizer merges\n"
                "agents:\n"
                "  financial:\n"
                "    model: gpt-4o\n"
                "    system_prompt: Analyze financial implications.\n"
                "  market:\n"
                "    model: gpt-4o\n"
                "    system_prompt: Analyze market trends and positioning.\n"
                "  risk:\n"
                "    model: gpt-4o\n"
                "    system_prompt: Identify and assess risks.\n"
                "workflow:\n"
                "  type: parallel\n"
                "  agents:\n"
                "    - financial\n"
                "    - market\n"
                "    - risk\n"
            ),
            "agents": ["financial", "market", "risk"],
        },
        {
            "name": "Iterative Refinement",
            "description": "An agent iterates on a task until quality threshold is met or max iterations reached.",
            "yaml": (
                "name: Iterative Refinement\n"
                "description: Loop until quality > 0.9\n"
                "agents:\n"
                "  improver:\n"
                "    model: gpt-4o\n"
                "    system_prompt: Improve the content. Rate your output 0-1 for quality.\n"
                "workflow:\n"
                "  type: loop\n"
                "  agent: improver\n"
                "  max_iterations: 5\n"
                "  stop_condition: quality_score > 0.9\n"
            ),
            "agents": ["improver"],
        },
        {
            "name": "Review & Approve",
            "description": "Generate content, review quality, then require human approval before publishing.",
            "yaml": (
                "name: Review Pipeline\n"
                "description: Generator → Reviewer → Approval → Publisher\n"
                "agents:\n"
                "  generator:\n"
                "    model: gpt-4o\n"
                "    system_prompt: Create high-quality content based on the brief.\n"
                "  reviewer:\n"
                "    model: gpt-4o-mini\n"
                "    system_prompt: Evaluate content quality. Score 1-10 and list issues.\n"
                "workflow:\n"
                "  type: sequential\n"
                "  steps:\n"
                "    - agent: generator\n"
                "    - agent: reviewer\n"
            ),
            "agents": ["generator", "reviewer"],
        },
        {
            "name": "Customer Support Triage",
            "description": "Classify incoming tickets by urgency and route to the right specialist team.",
            "yaml": (
                "name: Support Triage\n"
                "description: Classifier routes to specialist agents\n"
                "agents:\n"
                "  classifier:\n"
                "    model: gpt-4o-mini\n"
                "    system_prompt: Classify support ticket urgency as high/medium/low and category as billing/technical/general.\n"
                "  billing_agent:\n"
                "    model: gpt-4o\n"
                "    system_prompt: Handle billing inquiries with empathy and accuracy.\n"
                "  tech_agent:\n"
                "    model: gpt-4o\n"
                "    system_prompt: Troubleshoot technical issues step by step.\n"
                "workflow:\n"
                "  type: sequential\n"
                "  steps:\n"
                "    - agent: classifier\n"
                "    - agent: tech_agent\n"
            ),
            "agents": ["classifier", "billing_agent", "tech_agent"],
        },
    ]

    @app.get("/workflows/templates")
    async def workflow_templates() -> JSONResponse:
        return JSONResponse(_WORKFLOW_TEMPLATES)

    def _workflow_record_project_id(record: dict[str, Any]) -> str | None:
        pid = record.get("project_id")
        return pid if pid else None

    def _workflow_record_readable(record: dict[str, Any], request: Request) -> bool:
        rec_pid = _workflow_record_project_id(record)
        if _multi_ctx(request) is not None:
            return _in_read_scope(rec_pid, request)
        pid = _project_scope(request)
        return True if pid is None else rec_pid in (pid, None)

    def _workflow_runs_for_request(request: Request) -> list[dict[str, Any]]:
        runs = sf._read().get("workflow_runs", [])
        return [r for r in runs if _workflow_record_readable(r, request)]

    def _workflow_run_for_request(
        run_id: str, request: Request, *, write: bool = False
    ) -> dict[str, Any] | None:
        for record in sf._read().get("workflow_runs", []):
            if record.get("run_id") != run_id:
                continue
            ctx = _multi_ctx(request)
            if ctx is not None:
                require_in_project_scope(
                    _workflow_record_project_id(record), ctx, write=write
                )
            elif not _workflow_record_readable(record, request):
                return None
            return record
        return None

    @app.get("/workflows/stats")
    async def workflow_stats(request: Request) -> JSONResponse:
        runs = _workflow_runs_for_request(request)
        return JSONResponse({
            "queued": sum(1 for r in runs if r.get("status") == "queued"),
            "running": sum(1 for r in runs if r.get("status") == "running"),
            "completed": sum(1 for r in runs if r.get("status") == "completed"),
            "failed": sum(1 for r in runs if r.get("status") == "failed"),
            "workers": 0,
        })

    @app.get("/workflows/workers")
    async def workflow_workers() -> JSONResponse:
        return JSONResponse([])

    @app.post("/workflows/run")
    async def workflow_run(request: Request):
        """Execute a workflow — parse YAML, run each agent step, stream SSE."""
        import secrets as _sec
        import time as _wf_time
        import yaml as _yaml

        body = await request.json()
        pid = _project_scope(request)
        _enforce_run_quota(request)
        run_id = f"wf-{_sec.token_hex(6)}"
        yaml_str = body.get("yaml", body.get("yaml_content", ""))
        message = body.get("message", body.get("input", ""))
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        async def _execute():
            t0 = _wf_time.monotonic()
            steps = []
            agents_data = []
            full_output = ""
            events_log: list[dict[str, Any]] = []

            def _emit(event_type: str, payload: dict[str, Any]) -> str:
                """Record event for replay + format as SSE frame."""
                events_log.append({
                    "event_type": event_type,
                    "data": payload,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })
                return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"

            try:
                wf_def = _yaml.safe_load(yaml_str) if yaml_str else None
                if not wf_def or not isinstance(wf_def, dict):
                    yield _emit("workflow_error", {"error": "Invalid workflow YAML"})
                    return

                wf_name = wf_def.get("name", "unnamed")
                agents_defs = wf_def.get("agents", {})
                workflow_node = wf_def.get("workflow", {})

                yield _emit("workflow_started", {"run_id": run_id, "name": wf_name})

                # Extract agent steps from the workflow node
                agent_steps = []
                if workflow_node.get("type") == "sequential":
                    for step in workflow_node.get("steps", []):
                        if "agent" in step:
                            agent_steps.append(step["agent"])
                elif workflow_node.get("type") == "parallel":
                    agent_steps = workflow_node.get("agents", [])
                elif workflow_node.get("type") == "loop":
                    agent_steps = [workflow_node.get("agent", "")]
                elif "agent" in workflow_node:
                    agent_steps = [workflow_node["agent"]]

                if not agent_steps:
                    agent_steps = list(agents_defs.keys())

                yield _emit("workflow_steps", {"total": len(agent_steps), "agents": agent_steps})

                # Execute each agent step sequentially
                current_input = message
                for i, agent_name in enumerate(agent_steps):
                    agent_def = agents_defs.get(agent_name, {})
                    model = agent_def.get("model", "gpt-4o-mini")
                    system_prompt = agent_def.get("system_prompt", f"You are the {agent_name} agent.")
                    step_t0 = _wf_time.monotonic()
                    step_started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

                    yield _emit("step_started", {"step": i + 1, "agent": agent_name, "model": model})

                    # Call LLM via the active tenant provider resolution.
                    step_output = ""
                    step_status = "completed"
                    try:
                        import litellm
                        litellm.suppress_debug_info = True
                        messages = [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": current_input},
                        ]
                        completion_kwargs = await _litellm_completion_kwargs(request, model)
                        response = await litellm.acompletion(
                            **completion_kwargs, messages=messages, stream=True
                        )
                        async for chunk in response:
                            delta = chunk.choices[0].delta.content or ""
                            if delta:
                                step_output += delta
                                yield _emit("text_message_content", {"delta": delta, "agent": agent_name, "step": i + 1})

                    except Exception as exc:
                        step_status = "failed"
                        step_output = f"Error in {agent_name}: {exc}"
                        yield _emit("step_error", {"step": i + 1, "agent": agent_name, "error": str(exc)[:200]})

                    step_dt = _wf_time.monotonic() - step_t0
                    # Estimate tokens (~4 chars per token)
                    step_input_tokens = len(current_input) // 4
                    step_output_tokens = len(step_output) // 4
                    step_tokens = step_input_tokens + step_output_tokens
                    step_run_id = f"run-{_sec.token_hex(6)}"

                    steps.append({
                        "step": i + 1, "agent": agent_name, "model": model,
                        "duration_s": round(step_dt, 2),
                        "output_preview": step_output[:200],
                        "total_tokens": step_tokens,
                        "run_id": step_run_id,
                    })
                    agents_data.append({
                        "name": agent_name, "model": model,
                        "output": step_output, "duration_s": round(step_dt, 2),
                        "total_tokens": step_tokens,
                        "input_tokens": step_input_tokens,
                        "output_tokens": step_output_tokens,
                    })

                    # Record this step as an individual agent run so
                    # /agents/runs can surface inline workflow steps.
                    try:
                        completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        run_store = _run_store(request)
                        if run_store is not None:
                            await run_store.save_run_for(
                                request.state.context,
                                run_id=step_run_id,
                                agent_name=agent_name,
                                model=model,
                                status=step_status,
                                input_text=current_input,
                                output_text=step_output,
                                total_tokens=step_tokens,
                                input_tokens=step_input_tokens,
                                output_tokens=step_output_tokens,
                                duration_ms=int(step_dt * 1000),
                                started_at=_iso_to_epoch(step_started_at),
                                completed_at=_iso_to_epoch(completed_at),
                                run_type="workflow_step",
                                parent_workflow_run_id=run_id,
                                tool_calls=[],
                            )
                        else:
                            sf.save_agent_run({
                                "run_id": step_run_id,
                                "agent_name": agent_name,
                                "model": model,
                                "status": step_status,
                                "input_text": current_input,
                                "output_text": step_output,
                                "total_tokens": step_tokens,
                                "started_at": step_started_at,
                                "completed_at": completed_at,
                                "run_type": "workflow_step",
                                "parent_workflow_run_id": run_id,
                                "tool_calls": [],
                                "project_id": _owner(pid),
                            })
                    except Exception as persist_exc:
                        logger.error("Failed to persist workflow step run %s: %s", step_run_id, persist_exc)

                    yield _emit("step_completed", {"step": i + 1, "agent": agent_name, "duration_s": round(step_dt, 2)})

                    # Chain output → next agent's input
                    current_input = step_output
                    full_output = step_output  # Last agent's output is the final output

                elapsed = round(_wf_time.monotonic() - t0, 2)
                total_tokens = sum(a.get("total_tokens", 0) for a in agents_data)
                total_input_tokens = sum(a.get("input_tokens", 0) for a in agents_data)
                total_output_tokens = sum(a.get("output_tokens", 0) for a in agents_data)

                finished_payload = {
                    "output": full_output,
                    "elapsed_seconds": elapsed,
                    "agents": agents_data,
                    "total_steps": len(steps),
                    "run_id": run_id,
                    "total_tokens": total_tokens,
                    "total_input_tokens": total_input_tokens,
                    "total_output_tokens": total_output_tokens,
                }

                # Persist run to history. The UI expects `run.output` to
                # be a rich dict (used to extract stats), so store the
                # finished_payload rather than the plain string.
                run_record = {
                    "run_id": run_id, "status": "completed",
                    "workflow_name": wf_name, "yaml_content": yaml_str,
                    "input": message, "output": finished_payload,
                    "steps": steps, "project_id": _owner(pid),
                    "elapsed_seconds": elapsed,
                    "total_tokens": total_tokens,
                    "total_input_tokens": total_input_tokens,
                    "total_output_tokens": total_output_tokens,
                    "agents": agents_data,
                    "started_at": now,
                    "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "events": events_log,
                }
                def _apply(d):
                    d.setdefault("workflow_runs", []).insert(0, run_record)
                    d["workflow_runs"] = d["workflow_runs"][:100]
                sf.mutate(_apply)

                yield _emit("workflow_finished", finished_payload)

                logger.info("Workflow run %s completed in %.1fs (%d steps)",
                             run_id, elapsed, len(steps),
                             extra={"event": "workflow.run.completed", "run_id": run_id,
                                    "workflow_name": wf_name, "elapsed_s": elapsed})

            except Exception as exc:
                yield _emit("workflow_error", {"error": str(exc)})
                logger.error("Workflow run %s failed: %s", run_id, str(exc)[:200],
                             extra={"event": "workflow.run.error", "run_id": run_id})

        return StreamingResponse(
            _execute(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    @app.get("/workflows/runs/{run_id}")
    async def workflow_run_detail(run_id: str, request: Request) -> JSONResponse:
        record = _workflow_run_for_request(run_id, request)
        if record is not None:
            return JSONResponse(record)
        return JSONResponse({
            "run_id": run_id, "status": "not_found",
            "steps": [], "started_at": None, "finished_at": None,
        })

    @app.get("/workflows/runs/{run_id}/events")
    async def workflow_run_events(run_id: str, request: Request):
        """Stream stored events for a completed workflow run as SSE.

        Live events for an in-flight run arrive via the /workflows/run
        POST response. This endpoint is the replay-only path used by the
        history detail page to populate the Events tab for completed runs.
        """
        target = _workflow_run_for_request(run_id, request)

        async def _replay():
            if target is None:
                yield f"event: not_found\ndata: {json.dumps({'run_id': run_id})}\n\n"
                return
            for evt in target.get("events", []):
                payload = evt.get("data", {})
                yield f"event: {evt.get('event_type', 'message')}\ndata: {json.dumps(payload)}\n\n"
            # Signal stream end so the client stops polling
            yield "event: stream_end\ndata: {}\n\n"

        return StreamingResponse(
            _replay(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    @app.post("/workflows/runs/{run_id}/cancel")
    async def workflow_cancel(run_id: str, request: Request) -> JSONResponse:
        target = _workflow_run_for_request(run_id, request, write=True)
        if target is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        target_pid = _workflow_record_project_id(target)

        def _apply(d):
            for r in d.get("workflow_runs", []):
                if (
                    r.get("run_id") == run_id
                    and _workflow_record_project_id(r) == target_pid
                ):
                    r["status"] = "cancelled"
                    return True
            return False
        if sf.mutate(_apply):
            return JSONResponse({"status": "cancelled"})
        return JSONResponse({"status": "not_found"}, status_code=404)

    @app.post("/workflows/runs/{run_id}/approve")
    async def workflow_approve(run_id: str, request: Request) -> JSONResponse:
        target = _workflow_run_for_request(run_id, request, write=True)
        if target is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        target_pid = _workflow_record_project_id(target)

        def _apply(d):
            for r in d.get("workflow_runs", []):
                if (
                    r.get("run_id") == run_id
                    and _workflow_record_project_id(r) == target_pid
                ):
                    r["status"] = "approved"
                    r["approved_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    return True
            return False

        if sf.mutate(_apply):
            await _emit_audit(request, "workflow.approve", target_type="workflow_run", target_id=run_id)
            return JSONResponse({"status": "approved"})
        return JSONResponse({"status": "not_found"}, status_code=404)

    @app.post("/workflows/runs/{run_id}/reject")
    async def workflow_reject(run_id: str, request: Request) -> JSONResponse:
        target = _workflow_run_for_request(run_id, request, write=True)
        if target is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        target_pid = _workflow_record_project_id(target)

        def _apply(d):
            for r in d.get("workflow_runs", []):
                if (
                    r.get("run_id") == run_id
                    and _workflow_record_project_id(r) == target_pid
                ):
                    r["status"] = "rejected"
                    r["rejected_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    if body.get("reason"):
                        r["reject_reason"] = body.get("reason")
                    return True
            return False

        if sf.mutate(_apply):
            await _emit_audit(request, "workflow.reject", target_type="workflow_run", target_id=run_id)
            return JSONResponse({"status": "rejected"})
        return JSONResponse({"status": "not_found"}, status_code=404)

    @app.get("/workflows/history")
    async def workflow_history(request: Request) -> JSONResponse:
        return JSONResponse(_workflow_runs_for_request(request))

    @app.get("/workflows/history/{run_id}")
    async def workflow_history_detail(run_id: str, request: Request) -> JSONResponse:
        record = _workflow_run_for_request(run_id, request)
        if record is not None:
            return JSONResponse(record)
        return JSONResponse({
            "run_id": run_id, "status": "not_found",
            "steps": [], "started_at": None, "finished_at": None,
        })

    @app.get("/workflows/approvals")
    async def workflow_approvals() -> JSONResponse:
        return JSONResponse([])

    @app.get("/workflows/dlq")
    async def workflow_dlq() -> JSONResponse:
        return JSONResponse([])

    @app.post("/workflows/dlq/{run_id}/retry")
    async def workflow_dlq_retry(run_id: str, request: Request) -> JSONResponse:
        target = _workflow_run_for_request(run_id, request, write=True)
        if target is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        if target.get("status") != "failed":
            return JSONResponse({"detail": "workflow run is not failed"}, status_code=409)
        return JSONResponse(
            {"detail": "workflow DLQ retry is not implemented"},
            status_code=501,
        )

    @app.delete("/workflows/dlq/{run_id}")
    async def workflow_dlq_discard(run_id: str, request: Request) -> JSONResponse:
        target = _workflow_run_for_request(run_id, request, write=True)
        if target is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        if target.get("status") != "failed":
            return JSONResponse({"detail": "workflow run is not failed"}, status_code=409)
        target_pid = _workflow_record_project_id(target)

        def _apply(d):
            before = len(d.get("workflow_runs", []))
            d["workflow_runs"] = [
                r
                for r in d.get("workflow_runs", [])
                if not (
                    r.get("run_id") == run_id
                    and _workflow_record_project_id(r) == target_pid
                )
            ]
            return len(d.get("workflow_runs", [])) != before

        if sf.mutate(_apply):
            await _emit_audit(request, "workflow.dlq.discard", target_type="workflow_run", target_id=run_id)
            return JSONResponse({"status": "discarded"})
        return JSONResponse({"status": "not_found"}, status_code=404)

    @app.post("/workflows/dispatch")
    async def workflow_dispatch(request: Request) -> JSONResponse:
        return JSONResponse(
            {"detail": "workflow dispatch is not implemented; use /api/v1/workflows/enqueue"},
            status_code=501,
        )

    @app.post("/api/v1/workflows/enqueue", status_code=202)
    async def workflows_enqueue(request: Request) -> JSONResponse:
        """Canonical workflow-run enqueue endpoint matching architecture WorkflowRun shape.

        Accepts the canonical body shape (workflow_name, input_data, execution_mode,
        optional security_profile_ref, optional artifact_destination). Resolves the
        Sealed cascade when security_profile_ref is set, derives requires_sandbox_mode
        via sandbox_mode_for(), capability-checks against the fleet registry, persists
        a WorkflowRun row, and returns 202 Accepted with resolved metadata.

        Does NOT replace /workflows/run (YAML-shaped playground endpoint) or
        /workflows/runs/{run_id} (status polling).
        """
        import secrets as _enq_sec

        from fastapi import HTTPException as _HTTPException

        from sagewai.artifacts.models import ArtifactDestination as _ArtifactDestination
        from sagewai.core.state import ExecutionMode as _ExecutionMode, WorkflowRun as _WorkflowRun, sandbox_mode_for as _sandbox_mode_for
        from sagewai.fleet.models import WorkerApprovalStatus as _WorkerApprovalStatus
        from sagewai.sandbox.models import SandboxMode as _SandboxMode

        body = await request.json()
        pid = _project_scope(request)
        _enforce_run_quota(request)

        workflow_name = body.get("workflow_name")
        if not workflow_name:
            raise _HTTPException(status_code=400, detail="workflow_name is required")

        input_data = body.get("input_data", {})
        execution_mode_str = body.get("execution_mode", "full")
        security_profile_ref = body.get("security_profile_ref")
        artifact_destination_raw = body.get("artifact_destination")

        # 1. Validate execution_mode
        try:
            execution_mode = _ExecutionMode(execution_mode_str)
        except ValueError:
            valid = [m.value for m in _ExecutionMode]
            raise _HTTPException(
                status_code=400,
                detail=f"unknown execution_mode {execution_mode_str!r}; valid values: {valid}",
            )

        # 2. Derive sandbox mode requirement
        requires_sandbox_mode = _sandbox_mode_for(execution_mode)

        # 2b. Host-exec guard: reject bare/inline runs when policy disallows it.
        # Container images set SAGEWAI_RUNTIME=container; SAGEWAI_ALLOW_HOST_EXEC=1
        # overrides. Local/self-hosted deployments are allowed by default.
        if requires_sandbox_mode == _SandboxMode.NONE:
            from sagewai.sandbox.policy import host_exec_allowed
            if not host_exec_allowed():
                raise _HTTPException(
                    status_code=403,
                    detail=(
                        "Host-backed execution disabled. "
                        "Set SAGEWAI_ALLOW_HOST_EXEC=1 to enable."
                    ),
                )

        # 3. Capability check against fleet registry.
        # For non-BARE runs (requires_sandbox_mode=PER_RUN) we verify that at
        # least one APPROVED worker is registered. BARE runs execute inline and
        # need no worker.
        if requires_sandbox_mode != _SandboxMode.NONE:
            approved_workers = await fleet_registry.list_workers(
                org_id=_fleet_org_id(request),
                status=_WorkerApprovalStatus.APPROVED,
            )
            if not approved_workers:
                raise _HTTPException(
                    status_code=400,
                    detail=(
                        f"execution_mode={execution_mode_str!r} requires "
                        f"sandbox_mode={requires_sandbox_mode.value!r} but no approved "
                        "worker is registered in the fleet. Register and approve a worker "
                        "first, or use execution_mode='bare' for inline execution."
                    ),
                )

        # 4. Resolve Sealed cascade when security_profile_ref is set.
        # For execution_mode=FULL (Mode 3+) without a profile_ref, we also check
        # for a system-level default; if none exists, reject with 400.
        effective_env_keys: list[str] = []
        effective_secret_keys: list[str] = []

        if security_profile_ref:
            from sagewai.sealed.resolution import CascadeLevel as _CascadeLevel, resolve_security_profile as _resolve_security_profile

            sealed_cfg = sf.get_sealed_config()
            workflow_sealed_cfg = sf.get_workflow_sealed_config(workflow_name) or {}

            levels = [
                _CascadeLevel(
                    name="system",
                    profile_ref=sealed_cfg.get("system_profile_ref"),
                    overrides=sealed_cfg.get("system_overrides"),
                ),
                _CascadeLevel(
                    name="workflow",
                    profile_ref=workflow_sealed_cfg.get("profile_ref"),
                    overrides=workflow_sealed_cfg.get("overrides"),
                ),
                _CascadeLevel(
                    name="user",
                    profile_ref=security_profile_ref,
                    overrides=None,
                ),
            ]

            try:
                from sagewai.sealed.audit import AuditWriter as _AuditWriter
                eff = await _resolve_security_profile(
                    levels=levels,
                    audit_writer=None,  # audit via OTel structured log below
                    audit_context={
                        "workflow_name": workflow_name,
                        "project_id": _owner(pid),
                        "run_type": "enqueue",
                    },
                )
            except PermissionError as exc:
                raise _HTTPException(status_code=403, detail=str(exc)) from exc
            except Exception as exc:
                raise _HTTPException(
                    status_code=400,
                    detail=f"cascade resolution failed: {exc}",
                ) from exc

            effective_env_keys = sorted(eff.env.keys())
            effective_secret_keys = sorted(eff.secret_keys)

            # Audit profile.cascade.resolved via structured log (OTel-visible in Grafana)
            logger.info(
                "profile.cascade.resolved at enqueue",
                extra={
                    "event": "profile.cascade.resolved",
                    "workflow_name": workflow_name,
                    "security_profile_ref": security_profile_ref,
                    "effective_env_keys": effective_env_keys,
                    "effective_secret_keys": effective_secret_keys,
                    "project_id": pid or "global",
                },
            )

        elif execution_mode in (_ExecutionMode.FULL, _ExecutionMode.FULL_JIT):
            # Mode 3/3b without a user-supplied profile_ref — check for a
            # system-level default. If none is configured, the run cannot
            # safely be enqueued (it would have no identity at runtime).
            sealed_cfg = sf.get_sealed_config()
            system_profile_ref = sealed_cfg.get("system_profile_ref")
            if not system_profile_ref:
                raise _HTTPException(
                    status_code=400,
                    detail=(
                        f"execution_mode={execution_mode_str!r} requires a security_profile_ref "
                        "or a system-level default profile (configure one via "
                        "PUT /api/v1/admin/sealed/system)."
                    ),
                )
            # System default exists — effective keys will be resolved at runtime
            # by the worker; we leave them empty at enqueue time here.

        # 5. Parse artifact_destination if provided
        artifact_destination: _ArtifactDestination | None = None
        if artifact_destination_raw is not None:
            try:
                artifact_destination = _ArtifactDestination.model_validate(
                    artifact_destination_raw
                )
            except Exception as exc:
                raise _HTTPException(
                    status_code=400,
                    detail=f"invalid artifact_destination: {exc}",
                ) from exc

        # 6. Persist WorkflowRun row matching the on-disk WorkflowRun shape
        run_id = f"wf-{_enq_sec.token_hex(6)}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        run = _WorkflowRun(
            workflow_name=workflow_name,
            run_id=run_id,
            execution_mode=execution_mode,
            requires_sandbox_mode=requires_sandbox_mode,
            security_profile_ref=security_profile_ref,
            effective_env_keys=effective_env_keys,
            effective_secret_keys=effective_secret_keys,
            artifact_destination=artifact_destination,
            input_data=input_data,
            project_id=_owner(pid),
        )

        run_record = run.to_dict()
        # Supplement with admin-UI fields expected by /workflows/history
        run_record.update({
            "started_at": now_iso,
            "enqueued_at": now_iso,
            "run_type": "enqueue",
        })

        def _apply(d):
            d.setdefault("workflow_runs", []).insert(0, run_record)
            d["workflow_runs"] = d["workflow_runs"][:100]
        sf.mutate(_apply)

        logger.info(
            "Workflow run enqueued: %s workflow=%s mode=%s",
            run_id, workflow_name, execution_mode_str,
            extra={
                "event": "workflow.run.enqueued",
                "run_id": run_id,
                "workflow_name": workflow_name,
                "execution_mode": execution_mode_str,
                "requires_sandbox_mode": requires_sandbox_mode.value,
                "project_id": pid or "global",
            },
        )

        # 7. Return 202 Accepted with resolved metadata
        return JSONResponse(
            {
                "run_id": run_id,
                "status": run.status.value,
                "execution_mode": run.execution_mode.value,
                "requires_sandbox_mode": run.requires_sandbox_mode.value,
                "security_profile_ref": run.security_profile_ref,
                "effective_env_keys": run.effective_env_keys,
                "effective_secret_keys": run.effective_secret_keys,
            },
            status_code=202,
        )

    @app.post("/workflows/validate")
    async def workflow_validate(request: Request) -> JSONResponse:
        """Validate workflow YAML and return parsed metadata."""
        body = await request.json()
        yaml_str = body.get("yaml", "")
        if not yaml_str.strip():
            return JSONResponse({"valid": False, "error": "Empty workflow YAML"})
        try:
            import yaml as _yaml
            data = _yaml.safe_load(yaml_str)
            if not data or not isinstance(data, dict):
                return JSONResponse({"valid": False, "error": "Invalid YAML structure"})
            name = data.get("name", "")
            agents = list(data.get("agents", {}).keys()) if isinstance(data.get("agents"), dict) else []
            workflow = data.get("workflow")
            if not name:
                return JSONResponse({"valid": False, "error": "Missing 'name' field"})
            if not workflow:
                return JSONResponse({"valid": False, "error": "Missing 'workflow' field"})
            return JSONResponse({
                "valid": True,
                "errors": [],
                "name": name,
                "agents": agents,
                "description": data.get("description", ""),
            })
        except Exception as exc:
            return JSONResponse({"valid": False, "error": f"YAML parse error: {exc}"})

    @app.get("/workflows/registered-agents")
    async def workflow_registered_agents() -> JSONResponse:
        agents = sf.list_agents()
        return JSONResponse([a.get("name", "") for a in agents])

    @app.get("/workflow-events/stream")
    async def workflow_events_stream() -> StreamingResponse:
        async def _empty():
            yield "data: {}\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    # ── Workflow registry (saved workflows) ────────────────────────

    @app.get("/api/v1/workflow-registry")
    async def list_saved_workflows() -> JSONResponse:
        data = sf._read()
        workflows = data.get("saved_workflows", [])
        return JSONResponse({"items": workflows, "total": len(workflows)})

    @app.post("/api/v1/workflow-registry")
    async def save_workflow(request: Request) -> JSONResponse:
        # FLAG: saved_workflows rows carry no project_id column; until that
        # schema lands, gate the write to org owner/admin (safe interim) rather
        # than fake per-project scoping. No-op in single-org.
        require_org_admin(request.state.context)
        body = await request.json()
        import secrets as _sec
        wf = {
            "id": f"wf-{_sec.token_hex(6)}",
            "name": body.get("name", ""),
            "yaml_content": body.get("yaml_content", ""),
            "description": body.get("description", ""),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        def _apply(d):
            d.setdefault("saved_workflows", []).append(wf)
        sf.mutate(_apply)
        return JSONResponse(wf, status_code=201)

    @app.get("/api/v1/workflow-registry/by-name/{name}")
    async def get_saved_workflow_by_name(name: str) -> JSONResponse:
        data = sf._read()
        for wf in data.get("saved_workflows", []):
            if wf.get("name") == name:
                return JSONResponse(wf)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.get("/api/v1/workflow-registry/{wf_id}")
    async def get_saved_workflow(wf_id: str) -> JSONResponse:
        data = sf._read()
        for wf in data.get("saved_workflows", []):
            if wf.get("id") == wf_id:
                return JSONResponse(wf)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/workflow-registry/{wf_id}")
    async def delete_saved_workflow(wf_id: str, request: Request) -> JSONResponse:
        # FLAG: saved_workflows rows carry no project_id column (see save_workflow).
        require_org_admin(request.state.context)
        def _apply(d):
            wfs = d.get("saved_workflows", [])
            d["saved_workflows"] = [w for w in wfs if w.get("id") != wf_id]
        sf.mutate(_apply)
        return JSONResponse({"status": "ok"})

    # ── Budget limits ────────────────────────────────────────────

    @app.get("/api/v1/budget/limits")
    async def list_budget_limits() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("budget_limits", []))

    @app.post("/api/v1/budget/limits")
    async def create_budget_limit(request: Request) -> JSONResponse:
        # FLAG: budget_limits rows carry no project_id column; org-admin gate is
        # the safe interim until a project column lands. No-op in single-org.
        require_org_admin(request.state.context)
        body = await request.json()
        body.setdefault("created_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
        agent = body.get("agent_name", "")
        def _apply(d):
            limits = d.setdefault("budget_limits", [])
            d["budget_limits"] = [l for l in limits if l.get("agent_name") != agent]
            d["budget_limits"].append(body)
        sf.mutate(_apply)
        return JSONResponse(body, status_code=201)

    @app.put("/api/v1/budget/limits/{agent_name}")
    async def update_budget_limit(agent_name: str, request: Request) -> JSONResponse:
        # FLAG: budget_limits rows carry no project_id column (see create_budget_limit).
        require_org_admin(request.state.context)
        body = await request.json()
        def _apply(d):
            for l in d.get("budget_limits", []):
                if l.get("agent_name") == agent_name:
                    l.update(body)
                    return l
            return None
        result = sf.mutate(_apply)
        if result is not None:
            return JSONResponse(result)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/budget/limits/{agent_name}")
    async def delete_budget_limit(agent_name: str, request: Request) -> JSONResponse:
        # FLAG: budget_limits rows carry no project_id column (see create_budget_limit).
        require_org_admin(request.state.context)
        def _apply(d):
            limits = d.get("budget_limits", [])
            d["budget_limits"] = [l for l in limits if l.get("agent_name") != agent_name]
        sf.mutate(_apply)
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/budget/status/{agent_name}")
    async def get_budget_status(agent_name: str) -> JSONResponse:
        data = sf._read()
        limit = next((l for l in data.get("budget_limits", []) if l.get("agent_name") == agent_name), None)
        return JSONResponse({
            "agent_name": agent_name,
            "limit": limit,
            "current_spend_usd": 0.0,
            "remaining_usd": limit.get("daily_limit_usd", 0) if limit else 0,
            "status": "ok",
        })

    # ── Guardrail configs ────────────────────────────────────────

    @app.get("/api/v1/guardrails/configs")
    async def list_guardrail_configs() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("guardrail_configs", []))

    @app.get("/api/v1/guardrails/configs/{agent_name}")
    async def get_guardrail_config(agent_name: str) -> JSONResponse:
        data = sf._read()
        config = next((c for c in data.get("guardrail_configs", []) if c.get("agent_name") == agent_name), None)
        if config:
            return JSONResponse(config)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.put("/api/v1/guardrails/configs/{agent_name}")
    async def upsert_guardrail_config(agent_name: str, request: Request) -> JSONResponse:
        # FLAG: guardrail_configs rows carry no project_id column; org-admin gate
        # is the safe interim until a project column lands. No-op in single-org.
        require_org_admin(request.state.context)
        body = await request.json()
        body["agent_name"] = agent_name
        def _apply(d):
            configs = d.setdefault("guardrail_configs", [])
            d["guardrail_configs"] = [c for c in configs if c.get("agent_name") != agent_name]
            d["guardrail_configs"].append(body)
        sf.mutate(_apply)
        return JSONResponse(body)

    @app.delete("/api/v1/guardrails/configs/{agent_name}/{guardrail_type}")
    async def delete_guardrail_config(agent_name: str, guardrail_type: str, request: Request) -> JSONResponse:
        # FLAG: guardrail_configs rows carry no project_id column (see upsert_guardrail_config).
        require_org_admin(request.state.context)
        def _apply(d):
            for c in d.get("guardrail_configs", []):
                if c.get("agent_name") == agent_name:
                    types = c.get("guardrails", [])
                    c["guardrails"] = [g for g in types if g.get("type") != guardrail_type]
                    return True
            return False
        if sf.mutate(_apply):
            return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    # ── Audit events ─────────────────────────────────────────────

    @app.get("/api/v1/audit/events")
    async def list_audit_events() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("audit_events", []))

    @app.get("/api/v1/audit/export")
    async def export_audit_events() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("audit_events", []))

    # ── API tokens ───────────────────────────────────────────────

    @app.get("/api/v1/tokens/")
    async def list_tokens(request: Request) -> JSONResponse:
        if _is_multi_tenant():
            return JSONResponse(
                {"detail": "Tenant API tokens are not implemented"},
                status_code=501,
            )
        return JSONResponse(sf.list_api_tokens())

    @app.post("/api/v1/tokens/")
    async def create_token(request: Request) -> JSONResponse:
        require_org_admin(request.state.context)
        if _is_multi_tenant():
            return JSONResponse(
                {"detail": "Tenant API tokens are not implemented"},
                status_code=501,
            )
        body = await request.json()
        entry = sf.create_api_token(name=body.get("name", "Unnamed"),
                                    scopes=body.get("scopes", ["read"]))
        emit_audit(sf, event_type="token.created",
                   actor_label=request.state.principal.actor_label,
                   target=entry["id"], details={"scopes": entry["scopes"]})
        return JSONResponse(entry, status_code=201)

    @app.post("/api/v1/tokens/{token_id}/revoke")
    async def revoke_token(token_id: str, request: Request) -> JSONResponse:
        require_org_admin(request.state.context)
        if _is_multi_tenant():
            return JSONResponse(
                {"detail": "Tenant API tokens are not implemented"},
                status_code=501,
            )
        if sf.revoke_api_token(token_id):
            emit_audit(sf, event_type="token.revoked",
                       actor_label=request.state.principal.actor_label, target=token_id)
            return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/tokens/{token_id}")
    async def delete_token(token_id: str, request: Request) -> JSONResponse:
        require_org_admin(request.state.context)
        if _is_multi_tenant():
            return JSONResponse(
                {"detail": "Tenant API tokens are not implemented"},
                status_code=501,
            )
        if sf.delete_api_token(token_id):
            emit_audit(sf, event_type="token.deleted",
                       actor_label=request.state.principal.actor_label, target=token_id)
            return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    # ── Fleet (real SDK integration) ────────────────────────────
    # Uses InMemoryFleetRegistry + FleetDispatcher from the SDK.
    # Workers register, get approved, claim tasks by project/model/tags.

    from sagewai.fleet import (
        InMemoryFleetRegistry,
        InMemoryTaskStore,
        FleetDispatcher,
        WorkerCapabilities,
    )

    fleet_registry = InMemoryFleetRegistry()
    fleet_task_store = InMemoryTaskStore()
    fleet_dispatcher = FleetDispatcher(
        store=fleet_task_store, poll_timeout=5.0, poll_interval=1.0
    )

    def _fleet_org_id(request: Request) -> str:
        ctx = _multi_ctx(request)
        return ctx.org_id if ctx is not None else "default"

    async def _fleet_worker_in_scope(worker_id: str, request: Request):
        worker = await fleet_registry.get_worker(worker_id)
        if worker is None:
            return None
        ctx = _multi_ctx(request)
        if ctx is not None and getattr(worker, "org_id", None) != ctx.org_id:
            return None
        return worker

    @app.post("/api/v1/fleet/register")
    async def fleet_register(request: Request) -> JSONResponse:
        """Worker self-registration."""
        body = await request.json()
        pid = _project_scope(request)
        caps = WorkerCapabilities(
            models_supported=body.get("models", []),
            pool=body.get("pool", "default"),
            labels=body.get("labels", {}),
            max_concurrent=body.get("max_concurrent", 1),
        )
        # Add project_id as a label for scoped dispatch
        if pid:
            caps.labels["project_id"] = pid
        worker = await fleet_registry.register_worker(
            name=body.get("name", "worker"),
            org_id=_fleet_org_id(request),
            capabilities=caps,
            enrollment_key=body.get("enrollment_key"),
        )
        logger.info("Fleet worker registered: %s pool=%s models=%s",
                     worker.name, caps.pool, caps.models_supported,
                     extra={"event": "fleet.worker.registered", "worker_id": worker.id,
                            "pool": caps.pool, "project_id": pid or "global"})
        return JSONResponse({
            "worker_id": worker.id,
            "status": worker.approval_status.value,
            "capabilities": {
                "models": caps.models_supported,
                "pool": caps.pool,
                "labels": caps.labels,
                "max_concurrent": caps.max_concurrent,
            },
        }, status_code=201)

    @app.post("/api/v1/fleet/claim")
    async def fleet_claim(request: Request) -> JSONResponse:
        """Worker claims a task matching its capabilities."""
        body = await request.json()
        task = await fleet_dispatcher.claim(
            worker_id=body.get("worker_id", ""),
            org_id=_fleet_org_id(request),
            models_canonical=body.get("models", []),
            pool=body.get("pool", "default"),
            labels=body.get("labels"),
        )
        if task:
            return JSONResponse(task)
        # 204 must have no body; JSONResponse(None, ...) writes "null" and
        # crashes the h11 writer with "Too much data for declared Content-Length".
        return Response(status_code=204)

    @app.post("/api/v1/fleet/report")
    async def fleet_report(request: Request) -> JSONResponse:
        """Worker reports task completion."""
        body = await request.json()
        await fleet_dispatcher.report(
            worker_id=body.get("worker_id", ""),
            org_id=_fleet_org_id(request),
            run_id=body.get("run_id", ""),
            status=body.get("status", "completed"),
            output=body.get("output"),
            error=body.get("error"),
        )
        return JSONResponse({"status": "ok"})

    @app.post("/api/v1/fleet/heartbeat")
    async def fleet_heartbeat(request: Request) -> JSONResponse:
        """Worker heartbeat. Optionally carries pool_stats snapshot."""
        body = await request.json()
        pool_stats = body.get("pool_stats")
        await fleet_registry.heartbeat(
            body.get("worker_id", ""), pool_stats=pool_stats,
        )
        return JSONResponse({"ok": True})

    @app.get("/api/v1/fleet/workers")
    async def list_fleet_workers(request: Request) -> JSONResponse:
        workers = await fleet_registry.list_workers(org_id=_fleet_org_id(request))
        return JSONResponse([
            {
                "id": w.id,
                "name": w.name,
                "status": w.approval_status.value,
                "pool": w.capabilities.pool,
                "models": w.capabilities.models_supported,
                "labels": w.capabilities.labels,
                "max_concurrent": w.capabilities.max_concurrent,
                "last_heartbeat": w.last_heartbeat.isoformat() if w.last_heartbeat else None,
                "registered_at": w.registered_at.isoformat(),
            }
            for w in workers
        ])

    @app.get("/api/v1/fleet/workers/{worker_id}")
    async def get_fleet_worker(worker_id: str, request: Request) -> JSONResponse:
        w = await _fleet_worker_in_scope(worker_id, request)
        if not w:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return JSONResponse({
            "id": w.id, "name": w.name,
            "status": w.approval_status.value,
            "pool": w.capabilities.pool,
            "models": w.capabilities.models_supported,
            "labels": w.capabilities.labels,
            "max_concurrent": w.capabilities.max_concurrent,
            "last_heartbeat": w.last_heartbeat.isoformat() if w.last_heartbeat else None,
            "registered_at": w.registered_at.isoformat(),
        })

    @app.post("/api/v1/fleet/workers/{worker_id}/approve")
    async def approve_fleet_worker(worker_id: str, request: Request) -> JSONResponse:
        require_org_admin(request.state.context)  # fleet management is org-level
        from fastapi import HTTPException
        if await _fleet_worker_in_scope(worker_id, request) is None:
            raise HTTPException(status_code=404, detail="worker not found")
        try:
            w = await fleet_registry.approve_worker(worker_id, approved_by="admin")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        emit_audit(sf, event_type="fleet.worker.approved",
                   actor_label=request.state.principal.actor_label, target=worker_id)
        return JSONResponse({"status": w.approval_status.value, "worker_id": w.id})

    @app.post("/api/v1/fleet/workers/{worker_id}/reject")
    async def reject_fleet_worker(worker_id: str, request: Request) -> JSONResponse:
        require_org_admin(request.state.context)  # fleet management is org-level
        from fastapi import HTTPException
        if await _fleet_worker_in_scope(worker_id, request) is None:
            raise HTTPException(status_code=404, detail="worker not found")
        try:
            w = await fleet_registry.reject_worker(worker_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        emit_audit(sf, event_type="fleet.worker.rejected",
                   actor_label=request.state.principal.actor_label, target=worker_id)
        return JSONResponse({"status": w.approval_status.value, "worker_id": w.id})

    @app.post("/api/v1/fleet/workers/{worker_id}/revoke")
    async def revoke_fleet_worker(worker_id: str, request: Request) -> JSONResponse:
        require_org_admin(request.state.context)  # fleet management is org-level
        from fastapi import HTTPException
        if await _fleet_worker_in_scope(worker_id, request) is None:
            raise HTTPException(status_code=404, detail="worker not found")
        try:
            w = await fleet_registry.revoke_worker(worker_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        emit_audit(sf, event_type="fleet.worker.revoked",
                   actor_label=request.state.principal.actor_label, target=worker_id)
        return JSONResponse({"status": w.approval_status.value, "worker_id": w.id})

    @app.get("/api/v1/admin/fleet/workers/{worker_id}/pool-stats")
    async def get_worker_pool_stats(worker_id: str, request: Request) -> JSONResponse:
        """Return the latest pool_stats snapshot from the worker's heartbeat cache.

        Returns 404 if the worker is unknown.
        Returns the snapshot (or null payload if worker reported nothing yet).
        """
        worker = await _fleet_worker_in_scope(worker_id, request)
        if worker is None:
            return JSONResponse({"error": "worker not found"}, status_code=404)
        snap = await fleet_registry.get_pool_stats(worker_id)
        return JSONResponse(snap if snap else {"snapshot": None})

    @app.get("/api/v1/fleet/enrollment-keys")
    async def list_fleet_enrollment_keys(request: Request) -> JSONResponse:
        keys = await fleet_registry.list_enrollment_keys(org_id=_fleet_org_id(request))
        return JSONResponse([
            {
                "id": k.id, "name": k.name, "pool": ",".join(k.allowed_pools),
                "max_uses": k.max_uses, "uses": k.current_uses,
                "revoked": k.revoked,
                "created_at": k.created_at.isoformat(),
            }
            for k in keys
        ])

    @app.post("/api/v1/fleet/enrollment-keys")
    async def create_fleet_enrollment_key(request: Request) -> JSONResponse:
        require_org_admin(request.state.context)  # fleet management is org-level
        body = await request.json()
        key_record, raw_key = await fleet_registry.create_enrollment_key(
            org_id=_fleet_org_id(request),
            name=body.get("name", ""),
            created_by="admin",
            max_uses=body.get("max_uses"),
            allowed_pools=body.get("pools", [body.get("pool", "default")]),
            allowed_models=body.get("models", []),
        )
        emit_audit(sf, event_type="fleet.enrollment_key.created",
                   actor_label=request.state.principal.actor_label, target=key_record.id)
        return JSONResponse({
            "id": key_record.id, "key": raw_key, "name": key_record.name,
            "max_uses": key_record.max_uses,
        }, status_code=201)

    @app.delete("/api/v1/fleet/enrollment-keys/{key_id}")
    async def revoke_fleet_enrollment_key(key_id: str, request: Request) -> JSONResponse:
        require_org_admin(request.state.context)  # fleet management is org-level
        from fastapi import HTTPException
        try:
            await fleet_registry.revoke_enrollment_key(key_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        emit_audit(sf, event_type="fleet.enrollment_key.revoked",
                   actor_label=request.state.principal.actor_label, target=key_id)
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/fleet/audit")
    async def list_fleet_audit() -> JSONResponse:
        return JSONResponse([])

    # ── Sessions ─────────────────────────────────────────────────

    @app.get("/api/v1/sessions/{session_id}/messages")
    async def get_session_messages(session_id: str) -> JSONResponse:
        return JSONResponse({"session_id": session_id, "messages": []})

    # ── Account ──────────────────────────────────────────────────

    @app.get("/api/v1/account")
    async def get_account(request: Request) -> JSONResponse:
        # Authenticated by AuthMiddleware (request.state.principal); resolve from
        # the store so admin-scoped API tokens work, not just session cookies.
        if getattr(request.state, "principal", None) is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        if _is_multi_tenant():
            ctx = request.state.context
            user = await identity_store.get_user(ctx.org_id, ctx.actor.id) if identity_store else None
            if not user:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            memberships = await identity_store.list_memberships(ctx.org_id, ctx.actor.id)
            return JSONResponse(
                jsonable_encoder(_tenant_user_payload(user, ctx, memberships))
            )
        user = sf.get_admin_user()
        if not user:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return JSONResponse(user)

    @app.patch("/api/v1/account/profile")
    async def update_profile(request: Request) -> JSONResponse:
        body = await request.json()
        if _is_multi_tenant():
            require_org_admin(request.state.context)
            ctx = request.state.context
            user = await identity_store.update_user_profile(
                ctx.org_id, ctx.actor.id, name=body.get("display_name")
            )
            if user is None:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            memberships = await identity_store.list_memberships(ctx.org_id, ctx.actor.id)
            return JSONResponse(
                jsonable_encoder(_tenant_user_payload(user, ctx, memberships))
            )
        return JSONResponse(sf.update_admin_profile(display_name=body.get("display_name")))

    @app.post("/api/v1/account/password")
    async def change_password(request: Request) -> JSONResponse:
        body = await request.json()
        if _is_multi_tenant():
            require_org_admin(request.state.context)
            ctx = request.state.context
            user = await identity_store.verify_credentials(
                ctx.org_id, ctx.actor.label, body.get("current_password", "")
            )
            if user is None or user.get("id") != ctx.actor.id:
                return JSONResponse({"detail": "Current password is incorrect"}, status_code=400)
            await identity_store.set_password(ctx.org_id, ctx.actor.id, body.get("new_password", ""))
            return JSONResponse({"status": "ok"})
        if not sf.change_admin_password(body.get("current_password", ""),
                                        body.get("new_password", "")):
            return JSONResponse({"detail": "Current password is incorrect"}, status_code=400)
        emit_audit(sf, event_type="account.password_changed",
                   actor_label=request.state.principal.actor_label)
        return JSONResponse({"status": "ok"})

    # ── Connectors ───────────────────────────────────────────────

    @app.get("/api/v1/connectors")
    async def list_connectors() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("connectors", []))

    @app.post("/api/v1/connectors/{name}")
    async def save_connector(name: str, request: Request) -> JSONResponse:
        body = await request.json()
        body["name"] = name
        def _apply(d):
            connectors = d.setdefault("connectors", [])
            d["connectors"] = [c for c in connectors if c.get("name") != name]
            d["connectors"].append(body)
        sf.mutate(_apply)
        return JSONResponse(body)

    @app.post("/api/v1/connectors/{name}/test")
    async def test_connector(name: str) -> JSONResponse:
        return JSONResponse({"connected": True, "name": name})

    @app.delete("/api/v1/connectors/{name}")
    async def delete_connector(name: str) -> JSONResponse:
        def _apply(d):
            connectors = d.get("connectors", [])
            d["connectors"] = [c for c in connectors if c.get("name") != name]
        sf.mutate(_apply)
        return JSONResponse({"status": "ok"})

    # ── Notifications ────────────────────────────────────────────

    @app.get("/api/v1/notifications/channels")
    async def list_notification_channels() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("notification_channels", []))

    @app.post("/api/v1/notifications/channels")
    async def save_notification_channel(request: Request) -> JSONResponse:
        # FLAG: notification_channels rows carry no project_id column; org-admin
        # gate is the safe interim until a project column lands. No-op single-org.
        require_org_admin(request.state.context)
        import secrets as _sec
        body = await request.json()
        body.setdefault("id", f"ch-{_sec.token_hex(6)}")
        def _apply(d):
            channels = d.setdefault("notification_channels", [])
            d["notification_channels"] = [c for c in channels if c.get("id") != body["id"]]
            d["notification_channels"].append(body)
        sf.mutate(_apply)
        return JSONResponse(body, status_code=201)

    @app.delete("/api/v1/notifications/channels/{channel_id}")
    async def delete_notification_channel(channel_id: str, request: Request) -> JSONResponse:
        # FLAG: notification_channels rows carry no project_id column (see save).
        require_org_admin(request.state.context)
        def _apply(d):
            channels = d.get("notification_channels", [])
            d["notification_channels"] = [c for c in channels if c.get("id") != channel_id]
        sf.mutate(_apply)
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/notifications/triggers")
    async def list_notification_triggers() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("notification_triggers", []))

    @app.post("/api/v1/notifications/triggers")
    async def save_notification_trigger(request: Request) -> JSONResponse:
        # FLAG: notification_triggers rows carry no project_id column; org-admin
        # gate is the safe interim until a project column lands. No-op single-org.
        require_org_admin(request.state.context)
        import secrets as _sec
        body = await request.json()
        body.setdefault("id", f"tr-{_sec.token_hex(6)}")
        def _apply(d):
            triggers = d.setdefault("notification_triggers", [])
            d["notification_triggers"] = [t for t in triggers if t.get("id") != body["id"]]
            d["notification_triggers"].append(body)
        sf.mutate(_apply)
        return JSONResponse(body, status_code=201)

    @app.delete("/api/v1/notifications/triggers/{trigger_id}")
    async def delete_notification_trigger(trigger_id: str, request: Request) -> JSONResponse:
        # FLAG: notification_triggers rows carry no project_id column (see save).
        require_org_admin(request.state.context)
        def _apply(d):
            triggers = d.get("notification_triggers", [])
            d["notification_triggers"] = [t for t in triggers if t.get("id") != trigger_id]
        sf.mutate(_apply)
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/notifications/history")
    async def notification_history() -> JSONResponse:
        return JSONResponse([])

    @app.post("/api/v1/notifications/test")
    async def test_notification(request: Request) -> JSONResponse:
        """Send a test notification to the specified channel."""
        body = await request.json()
        channel_type = body.get("channel_type", "")

        # Find the saved channel config
        data = sf._read()
        channels = data.get("notification_channels", [])
        channel = next(
            (c for c in channels if c.get("channel_type") == channel_type),
            None,
        )

        if channel_type == "slack":
            webhook_url = channel.get("webhook_url", "") if channel else body.get("webhook_url", "")
            if not webhook_url:
                return JSONResponse({"sent": False, "error": "No Slack webhook URL configured. Go to System → Notifications to add one."})
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(webhook_url, json={
                        "text": ":white_check_mark: *Sagewai Notification Test*\n\nThis is a test message from your Sagewai admin panel.\n\n_If you see this, your Slack webhook is configured correctly._",
                    })
                    if resp.status_code == 200:
                        logger.info("Slack webhook test sent successfully",
                                    extra={"event": "notification.test.success", "channel": "slack"})
                        return JSONResponse({"sent": True})
                    else:
                        logger.warning("Slack webhook test failed: %d %s", resp.status_code, resp.text[:100],
                                       extra={"event": "notification.test.failed", "channel": "slack"})
                        return JSONResponse({"sent": False, "error": f"Slack returned {resp.status_code}: {resp.text[:200]}"})
            except Exception as exc:
                return JSONResponse({"sent": False, "error": str(exc)})

        elif channel_type == "email":
            # Email via API-key providers (Resend, Postmark, SendGrid).
            # Provider is auto-detected from env vars or channel config.
            email_to = channel.get("email", "") if channel else body.get("email", "")
            if not email_to:
                return JSONResponse({"sent": False, "error": "No email address configured. Go to System → Notifications to add one."})

            # Resolve provider + API key from channel config or env
            provider = (channel or {}).get("email_provider", "") or os.environ.get("EMAIL_PROVIDER", "")
            api_key = (channel or {}).get("email_api_key", "") or os.environ.get("EMAIL_API_KEY", "")
            from_email = (channel or {}).get("email_from", "") or os.environ.get("EMAIL_FROM", "")
            # Resend requires a verified domain. Use their test address
            # if no custom from-address is configured.
            if not from_email and provider == "resend":
                from_email = "onboarding@resend.dev"
            elif not from_email:
                from_email = "notifications@sagewai.ai"

            # Auto-detect provider from API key prefix
            if not provider and api_key:
                if api_key.startswith("re_"):
                    provider = "resend"
                elif api_key.startswith("SG."):
                    provider = "sendgrid"
                else:
                    provider = "postmark"

            if not api_key:
                return JSONResponse({
                    "sent": False,
                    "error": "No email API key configured. Set EMAIL_API_KEY env var or configure in channel settings. Supported: Resend (re_*), Postmark, SendGrid (SG.*).",
                }, status_code=400)

            subject = "Sagewai Notification Test"
            html_body = (
                "<h2>Sagewai Notification Test</h2>"
                "<p>This is a test message from your Sagewai admin panel.</p>"
                "<p>If you received this, your email notifications are configured correctly.</p>"
                "<hr><p style='color:#888;font-size:12px'>Sent by Sagewai &mdash; Agent Infrastructure You Own</p>"
            )

            try:
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    if provider == "resend":
                        resp = await client.post(
                            "https://api.resend.com/emails",
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json={"from": from_email, "to": [email_to], "subject": subject, "html": html_body},
                        )
                    elif provider == "sendgrid":
                        resp = await client.post(
                            "https://api.sendgrid.com/v3/mail/send",
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json={
                                "personalizations": [{"to": [{"email": email_to}]}],
                                "from": {"email": from_email},
                                "subject": subject,
                                "content": [{"type": "text/html", "value": html_body}],
                            },
                        )
                    elif provider == "postmark":
                        resp = await client.post(
                            "https://api.postmarkapp.com/email",
                            headers={"X-Postmark-Server-Token": api_key, "Content-Type": "application/json"},
                            json={"From": from_email, "To": email_to, "Subject": subject, "HtmlBody": html_body},
                        )
                    else:
                        return JSONResponse({"sent": False, "error": f"Unknown email provider: {provider}"}, )

                    if resp.status_code in (200, 201, 202):
                        logger.info("Email test sent via %s to %s", provider, email_to,
                                    extra={"event": "notification.test.success", "channel": "email", "provider": provider})
                        return JSONResponse({"sent": True, "provider": provider})
                    else:
                        logger.warning("Email test failed via %s: %d", provider, resp.status_code,
                                       extra={"event": "notification.test.failed", "channel": "email", "provider": provider})
                        return JSONResponse({"sent": False, "error": f"{provider} returned {resp.status_code}: {resp.text[:200]}"})
            except Exception as exc:
                return JSONResponse({"sent": False, "error": str(exc)})

        else:
            return JSONResponse({"sent": True, "note": f"Channel type '{channel_type}' — logged only (no delivery endpoint)"})

    # ── Send notification (for agent tools) ────────────────────

    @app.post("/api/v1/notifications/send")
    async def send_notification(request: Request) -> JSONResponse:
        """Send a notification — used by agent tools (send_email, send_slack).

        Body params:
          channel: "email" | "slack"
          # For email:
          to: str (recipient email)
          subject: str
          body: str (HTML or plain text)
          # For slack:
          message: str
          webhook_url: str (optional — uses saved channel config if omitted)
          channel_name: str (optional — overrides #channel in message)
        """
        body = await request.json()
        ch = body.get("channel", "")

        if ch == "slack":
            webhook_url = body.get("webhook_url", "")
            if not webhook_url:
                # Fall back to saved channel config
                data = sf._read()
                saved = next((c for c in data.get("notification_channels", []) if c.get("channel_type") == "slack"), None)
                webhook_url = (saved or {}).get("webhook_url", "")
            if not webhook_url:
                return JSONResponse({"sent": False, "error": "No Slack webhook URL"}, status_code=400)
            message = body.get("message", "")
            channel_name = body.get("channel_name", "")
            payload: dict[str, Any] = {"text": message}
            if channel_name:
                payload["channel"] = channel_name
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(webhook_url, json=payload)
                return JSONResponse({"sent": resp.status_code == 200, "status_code": resp.status_code})
            except Exception as exc:
                return JSONResponse({"sent": False, "error": str(exc)})

        elif ch == "email":
            # Use the same API-key email logic as test_notification
            email_to = body.get("to", "")
            subject = body.get("subject", "Sagewai Notification")
            html_body = body.get("body", "")
            if not email_to:
                return JSONResponse({"sent": False, "error": "No recipient (to) specified"}, status_code=400)

            data = sf._read()
            saved = next((c for c in data.get("notification_channels", []) if c.get("channel_type") == "email"), None)
            provider = (saved or {}).get("email_provider", "") or os.environ.get("EMAIL_PROVIDER", "")
            api_key = (saved or {}).get("email_api_key", "") or os.environ.get("EMAIL_API_KEY", "")
            from_email = (saved or {}).get("email_from", "") or os.environ.get("EMAIL_FROM", "")
            if not from_email and provider == "resend":
                from_email = "onboarding@resend.dev"
            elif not from_email:
                from_email = "notifications@sagewai.ai"

            if not api_key:
                return JSONResponse({"sent": False, "error": "No EMAIL_API_KEY configured"}, )
            if not provider and api_key:
                if api_key.startswith("re_"):
                    provider = "resend"
                elif api_key.startswith("SG."):
                    provider = "sendgrid"
                else:
                    provider = "postmark"

            try:
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    if provider == "resend":
                        to_list = [email_to] if isinstance(email_to, str) else email_to
                        resp = await client.post(
                            "https://api.resend.com/emails",
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json={"from": from_email, "to": to_list, "subject": subject, "html": html_body},
                        )
                    elif provider == "sendgrid":
                        resp = await client.post(
                            "https://api.sendgrid.com/v3/mail/send",
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json={
                                "personalizations": [{"to": [{"email": email_to}]}],
                                "from": {"email": from_email},
                                "subject": subject,
                                "content": [{"type": "text/html", "value": html_body}],
                            },
                        )
                    elif provider == "postmark":
                        resp = await client.post(
                            "https://api.postmarkapp.com/email",
                            headers={"X-Postmark-Server-Token": api_key, "Content-Type": "application/json"},
                            json={"From": from_email, "To": email_to, "Subject": subject, "HtmlBody": html_body},
                        )
                    else:
                        return JSONResponse({"sent": False, "error": f"Unknown provider: {provider}"}, status_code=400)
                return JSONResponse({"sent": resp.status_code in (200, 201, 202), "provider": provider})
            except Exception as exc:
                return JSONResponse({"sent": False, "error": str(exc)})

        else:
            return JSONResponse({"sent": False, "error": f"Unknown channel: {ch}"}, status_code=400)

    # ── Triggers ─────────────────────────────────────────────────

    @app.get("/api/v1/triggers")
    async def list_triggers() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("triggers", []))

    @app.post("/api/v1/triggers")
    async def create_trigger(request: Request) -> JSONResponse:
        # FLAG: triggers rows carry no project_id column; org-admin gate is the
        # safe interim until a project column lands. No-op in single-org.
        require_org_admin(request.state.context)
        import secrets as _sec
        body = await request.json()
        body.setdefault("id", f"trig-{_sec.token_hex(6)}")
        def _apply(d):
            d.setdefault("triggers", []).append(body)
        sf.mutate(_apply)
        return JSONResponse(body, status_code=201)

    @app.delete("/api/v1/triggers/{trigger_id}")
    async def delete_trigger(trigger_id: str, request: Request) -> JSONResponse:
        # FLAG: triggers rows carry no project_id column (see create_trigger).
        require_org_admin(request.state.context)
        def _apply(d):
            triggers = d.get("triggers", [])
            d["triggers"] = [t for t in triggers if t.get("id") != trigger_id]
        sf.mutate(_apply)
        return JSONResponse({"status": "ok"})

    @app.patch("/api/v1/triggers/{trigger_id}/enable")
    async def enable_trigger(trigger_id: str, request: Request) -> JSONResponse:
        # FLAG: triggers rows carry no project_id column (see create_trigger).
        require_org_admin(request.state.context)
        def _apply(d):
            for t in d.get("triggers", []):
                if t.get("id") == trigger_id:
                    t["enabled"] = True
                    return t
            return None
        result = sf.mutate(_apply)
        if result is not None:
            return JSONResponse(result)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.patch("/api/v1/triggers/{trigger_id}/disable")
    async def disable_trigger(trigger_id: str, request: Request) -> JSONResponse:
        # FLAG: triggers rows carry no project_id column (see create_trigger).
        require_org_admin(request.state.context)
        def _apply(d):
            for t in d.get("triggers", []):
                if t.get("id") == trigger_id:
                    t["enabled"] = False
                    return t
            return None
        result = sf.mutate(_apply)
        if result is not None:
            return JSONResponse(result)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    # ── Analytics extras ─────────────────────────────────────────

    @app.get("/api/v1/analytics/workflow-heatmap")
    async def analytics_workflow_heatmap() -> JSONResponse:
        return JSONResponse({"data": []})

    @app.get("/api/v1/analytics/agent-network")
    async def analytics_agent_network() -> JSONResponse:
        return JSONResponse({"nodes": [], "edges": []})

    # ── MCP ──────────────────────────────────────────────────────

    @app.get("/api/v1/mcp/servers")
    async def list_mcp_servers() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("mcp_servers", []))

    @app.post("/api/v1/mcp/discover")
    async def discover_mcp_tools(request: Request) -> JSONResponse:
        return JSONResponse({"tools": []})

    @app.post("/api/v1/mcp/call")
    async def call_mcp_tool(request: Request) -> JSONResponse:
        return JSONResponse({"result": None, "error": "MCP server not connected"}, status_code=501)

    # ── Context Engine ───────────────────────────────────────────

    @app.get("/api/v1/context/stats")
    async def context_stats() -> JSONResponse:
        return JSONResponse({"documents": 0, "chunks": 0, "vectors": 0})

    @app.get("/api/v1/context/scopes")
    async def context_scopes() -> JSONResponse:
        return JSONResponse([])

    @app.get("/api/v1/context/documents")
    async def list_context_documents() -> JSONResponse:
        return JSONResponse([])

    @app.post("/api/v1/context/search")
    async def context_search(request: Request) -> JSONResponse:
        return JSONResponse({"results": []})

    # ── Memory ───────────────────────────────────────────────────

    @app.get("/api/v1/memory/vector/stats")
    async def vector_stats() -> JSONResponse:
        return JSONResponse({"total_vectors": 0, "collections": []})

    @app.post("/api/v1/memory/vector/search")
    async def vector_search(request: Request) -> JSONResponse:
        return JSONResponse({"results": []})

    @app.post("/api/v1/memory/vector/ingest")
    async def vector_ingest(request: Request) -> JSONResponse:
        # FLAG: stub endpoint with no backing store / no project_id column;
        # org-admin gate is the safe interim until a project-scoped memory store
        # lands. No-op in single-org.
        require_org_admin(request.state.context)
        return JSONResponse({"status": "ok", "chunks": 0})

    @app.get("/api/v1/memory/graph/stats")
    async def graph_stats() -> JSONResponse:
        return JSONResponse({"total_entities": 0, "total_relations": 0})

    @app.post("/api/v1/memory/graph/query")
    async def graph_query(request: Request) -> JSONResponse:
        return JSONResponse({"entities": [], "relations": []})

    @app.post("/api/v1/memory/graph/entity")
    async def create_graph_entity(request: Request) -> JSONResponse:
        # FLAG: stub endpoint, no backing store / no project_id column (see vector_ingest).
        require_org_admin(request.state.context)
        return JSONResponse({"status": "ok", "entity": ""})

    @app.post("/api/v1/memory/graph/relation")
    async def create_graph_relation(request: Request) -> JSONResponse:
        # FLAG: stub endpoint, no backing store / no project_id column (see vector_ingest).
        require_org_admin(request.state.context)
        return JSONResponse({"status": "ok", "relation": ""})

    @app.get("/api/v1/memory/graph/entities")
    async def list_graph_entities() -> JSONResponse:
        return JSONResponse({"entities": [], "count": 0})

    @app.get("/api/v1/memory/graph/entity/{name}")
    async def get_graph_entity(name: str) -> JSONResponse:
        return JSONResponse({"name": name, "metadata": {}})

    @app.get("/api/v1/memory/graph/entity/{name}/neighbors")
    async def get_graph_neighbors(name: str) -> JSONResponse:
        return JSONResponse({"entities": []})

    @app.get("/api/v1/memory/graph/entity/{name}/relations")
    async def get_graph_relations(name: str) -> JSONResponse:
        return JSONResponse({"relations": []})

    # ── Eval ─────────────────────────────────────────────────────

    @app.get("/api/v1/eval/datasets")
    async def list_eval_datasets() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("eval_datasets", []))

    @app.post("/api/v1/eval/datasets")
    async def create_eval_dataset(request: Request) -> JSONResponse:
        # FLAG: eval_datasets rows carry no project_id column; org-admin gate is
        # the safe interim until a project column lands. No-op in single-org.
        require_org_admin(request.state.context)
        import secrets as _sec
        body = await request.json()
        body.setdefault("id", f"ds-{_sec.token_hex(6)}")
        body.setdefault("created_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
        def _apply(d):
            d.setdefault("eval_datasets", []).append(body)
        sf.mutate(_apply)
        return JSONResponse(body, status_code=201)

    @app.get("/api/v1/eval/datasets/{dataset_id}")
    async def get_eval_dataset(dataset_id: str) -> JSONResponse:
        data = sf._read()
        ds = next((d for d in data.get("eval_datasets", []) if d.get("id") == dataset_id), None)
        if ds:
            return JSONResponse(ds)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/eval/datasets/{dataset_id}")
    async def delete_eval_dataset(dataset_id: str, request: Request) -> JSONResponse:
        # FLAG: eval_datasets rows carry no project_id column (see create_eval_dataset).
        require_org_admin(request.state.context)
        def _apply(d):
            datasets = d.get("eval_datasets", [])
            d["eval_datasets"] = [ds for ds in datasets if ds.get("id") != dataset_id]
        sf.mutate(_apply)
        return JSONResponse({"status": "ok"})

    @app.post("/api/v1/eval/run")
    async def run_eval(request: Request) -> JSONResponse:
        return JSONResponse({"detail": "Evaluation requires a running agent with LLM keys"}, status_code=501)

    @app.get("/api/v1/eval/runs")
    async def list_eval_runs() -> JSONResponse:
        return JSONResponse([])

    @app.get("/api/v1/eval/runs/{run_id}")
    async def get_eval_run(run_id: str) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    # ── Agent templates ──────────────────────────────────────────

    @app.get("/api/v1/agents/templates")
    async def list_templates() -> JSONResponse:
        return JSONResponse(_load_templates())

    @app.get("/api/v1/agents/templates/{template_id}")
    async def get_template(template_id: str) -> JSONResponse:
        for t in _load_templates():
            if t["id"] == template_id:
                return JSONResponse(t)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    # ── Health ───────────────────────────────────────────────────

    @app.get("/api/v1/health/summary")
    async def health_summary() -> JSONResponse:
        return JSONResponse({"status": "healthy", "sdk_version": version})

    @app.get("/api/v1/health/detailed")
    async def health_detailed() -> JSONResponse:
        return JSONResponse({
            "status": "healthy",
            "sdk_version": version,
            "checked_at": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "services": [],
        })

    # ── Billing (self-hosted: no provider) ────────────────────────

    @app.get("/api/v1/billing/plans")
    async def billing_plans() -> JSONResponse:
        return JSONResponse([])

    @app.get("/api/v1/billing/subscription")
    async def billing_subscription() -> JSONResponse:
        return JSONResponse(None, status_code=204)

    @app.get("/api/v1/billing/usage")
    async def billing_usage() -> JSONResponse:
        return JSONResponse(None, status_code=204)

    @app.get("/api/v1/billing/invoices")
    async def billing_invoices() -> JSONResponse:
        return JSONResponse([])

    @app.post("/api/v1/billing/portal")
    async def billing_portal() -> JSONResponse:
        return JSONResponse(
            {"detail": "No billing provider configured (self-hosted instance)"},
            status_code=501,
        )

    @app.post("/api/v1/billing/checkout")
    async def billing_checkout() -> JSONResponse:
        return JSONResponse(
            {"detail": "No billing provider configured (self-hosted instance)"},
            status_code=501,
        )

    # ── License ──────────────────────────────────────────────────

    @app.get("/license")
    async def license_info() -> JSONResponse:
        return JSONResponse({"type": "agpl", "valid": True})

    # ── OTel (optional) ──────────────────────────────────────────

    _init_otel(app, version)

    return app


# ── Helpers ──────────────────────────────────────────────────────────


async def _require_tenant_provider_key_if_encrypted(provider_store) -> None:
    """Fail closed at startup: if any encrypted tenant provider secret exists, the
    org master key must resolve (raises MasterKeyMissing/AdminKeyMissing otherwise).

    The single-org file-store equivalent is ``sf.require_secret_key_if_encrypted()``,
    run synchronously at app build; the tenant store is only available once the
    lifespan has built it, so this async check runs there.
    """
    if provider_store is None:
        return
    if await provider_store.has_encrypted_secrets():
        from sagewai.admin import tenant_keys

        tenant_keys.org_crypto()  # resolves the org master key; raises if unavailable


def _extract_token(request: Request) -> str | None:
    """Get auth token from header or cookie."""
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:]
    return request.cookies.get("sagewai_auth")


def _project_id(request: Request) -> str | None:
    """Extract project scope from X-Project-ID header or query param.

    Returns None for org-global scope (no filtering).
    """
    pid = request.headers.get("x-project-id") or request.query_params.get("project_id")
    return pid if pid else None


def _project_scope(request: Request) -> str | None:
    """The **filter** scope for a route's reads/deletes.

    In **multi-tenant** mode this derives from the session-validated
    RequestContext (the middleware already 404'd a forged/foreign
    ``X-Project-ID`` and resolves a project-only user's single project):
    a project context returns that project id (store: own + org-shared); an
    **org context returns ``SHARED_ONLY``** so the file store returns org-shared
    rows only — never the legacy ``None``-means-all-projects view. In
    **single-org** mode it is the header/query filter (unchanged foundation
    behaviour, ``None`` = no filter).

    Use :func:`_owner` to turn this back into the value to *stamp* on a new row
    (``SHARED_ONLY`` -> ``None`` = an org-shared row).
    """
    ctx = getattr(request.state, "context", None)
    if ctx is not None and ctx.tenancy_mode == "multi":
        return ctx.project_id if ctx.project_id is not None else SHARED_ONLY
    return _project_id(request)


def _provider_store(request: Request):
    """The active Postgres provider store, or None in single-org mode.

    When present, the provider routes use it (ctx-scoped) instead of the file
    store; when None they keep their unchanged ``sf.*`` path."""
    return _tenant_store_or_none(request, "provider")


def _agent_store(request: Request):
    """The active Postgres agent store, or None in single-org mode.

    When present, the playground-agent routes use it (ctx-scoped) instead of the
    file store; when None they keep their unchanged ``sf.*`` path."""
    return _tenant_store_or_none(request, "agent")


def _run_store(request: Request):
    """The active Postgres run store, or None in single-org mode.

    When present, the run routes use it (ctx-scoped) instead of the file store;
    when None they keep their unchanged ``sf.*`` path."""
    return _tenant_store_or_none(request, "run")


def _prompt_store(request: Request):
    """The active Postgres prompt-log store, or None in single-org mode.

    When present, the prompt-log routes use it (ctx-scoped) instead of the file
    store; when None they keep their unchanged ``sf.*`` path."""
    return _tenant_store_or_none(request, "prompt_log")


def _tenant_store_or_none(request: Request, name: str):
    rs = getattr(request.app.state, "resource_stores", None)
    store = getattr(rs, name, None) if rs is not None else None
    ctx = getattr(request.state, "context", None)
    if (
        store is None
        and ctx is not None
        and getattr(ctx, "tenancy_mode", None) == "multi"
    ):
        raise _TenantStoreUnavailableError(name)
    return store


def _owner(scope: str | None) -> str | None:
    """Convert a filter scope (:func:`_project_scope`) to the project_id to
    *stamp* on a newly-created row. ``SHARED_ONLY`` becomes ``None`` (an
    org-shared row); a concrete project id and ``None`` pass through."""
    return None if scope == SHARED_ONLY else scope


def _in_read_scope(item_project_id: str | None, request: Request) -> bool:
    """True if a stored item (by its project_id) is readable in the ctx scope.

    Project context: own project + org-shared (inherited). Org context:
    org-shared only. Single-org mode: always True (no boundary)."""
    ctx = _multi_ctx(request)
    if ctx is None:
        return True
    if ctx.project_id is None:
        return item_project_id in (None, "")
    return item_project_id in (ctx.project_id, None, "")


def _in_write_scope(item_project_id: str | None, request: Request) -> bool:
    """True if a stored item may be MUTATED in the ctx scope — own project only
    (org-shared rows are not mutable from a project context). Single mode: True."""
    ctx = _multi_ctx(request)
    if ctx is None:
        return True
    if ctx.project_id is None:
        return item_project_id in (None, "")
    return item_project_id == ctx.project_id


# Durable W8 audit is emitted for the tenant-scoped resource routes this
# initiative hardened: provider upsert/delete/set-default, agent create/delete,
# prompt-log create/update/delete. Other sensitive mutators (org/project
# settings, agent rename/run, workflow registry/run approvals, budget/guardrail,
# API-token CRUD, fleet approvals/enrollment-keys, connector/notification/trigger
# writes, memory ingest) still use the foundation's file audit — extending
# durable per-tenant audit to those is the explicit W8-coverage follow-up. This
# PR's durable-audit scope is intentionally the list above, not "all writes".
class _AuditUnavailableError(Exception):
    """Durable audit could not be recorded — the write fails closed (HTTP 503)."""


class _TenantStoreUnavailableError(Exception):
    """A multi-tenant resource route has no tenant store wired."""

    def __init__(self, store_name: str) -> None:
        super().__init__(store_name)
        self.store_name = store_name


async def _emit_audit(
    request: Request,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Append a durable, per-tenant, hash-chained audit event for a sensitive
    action (multi-tenant only; W8). **Fail-closed:** in multi-tenant mode a
    sensitive write is not reported as success unless its audit event is durably
    recorded — an append failure raises ``_AuditUnavailableError`` (-> HTTP 503)
    so the caller retries (provider/agent/log mutations are idempotent). No-op in
    single-org mode. Callers emit *after* the mutation, so on failure the row may
    be persisted but the operation is reported failed; an idempotent retry
    reconciles it (mutation no-op + audit append)."""
    ctx = _multi_ctx(request)
    if ctx is None:
        return
    store = getattr(getattr(request.app, "state", None), "tenant_audit", None)
    if store is None:
        from sagewai.admin.tenant_audit import TenantAuditStore

        store = TenantAuditStore()
        request.app.state.tenant_audit = store  # cache for subsequent emits
    try:
        await store.append(
            ctx.org_id,
            ctx.project_id,
            action,
            actor_user_id=ctx.actor.id,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata,
        )
    except Exception as exc:
        logger.error(
            "durable audit append failed for %s — failing the write closed",
            action,
            exc_info=True,
        )
        raise _AuditUnavailableError(action) from exc


class _QuotaExceededError(Exception):
    """A per-project resource quota was exceeded — HTTP 429."""


class _RunProjectRequiredError(Exception):
    """A non-admin tried to start a run without a concrete project — HTTP 409.

    Forcing run-start into a project (rather than the shared org scope) ensures
    org-shared execution is charged to the actor's project quota bucket, closing
    the org-bucket bypass."""


class _ProjectRunThrottle:
    """In-memory per-project run-start rate limiter (single-process by contract,
    like the foundation login throttle): caps run-starts per sliding window so one
    project cannot starve others. ``limit <= 0`` disables it (always allow)."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[Any, deque] = defaultdict(deque)

    def allow(self, key: Any) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        dq = self._hits[key]
        while dq and dq[0] < now - self.window:
            dq.popleft()
        if len(dq) >= self.limit:
            return False
        dq.append(now)
        return True


def _run_quota_limit() -> int:
    try:
        return int(os.environ.get("SAGEWAI_PROJECT_RUN_RATE", "0"))
    except ValueError:
        return 0


def _run_quota_window() -> float:
    try:
        return float(os.environ.get("SAGEWAI_PROJECT_RUN_WINDOW", "60"))
    except ValueError:
        return 60.0


def _enforce_run_quota(request: Request) -> None:
    """Per-project run-start rate quota (multi-tenant only; W7).

    Non-admin run-start requires a concrete project context: an org:member who
    omits X-Project-ID would otherwise land in the shared ``(org, None)`` bucket
    and bypass their per-project rate, so we require a selected project and charge
    org-shared execution to *that* project's bucket. Org owners/admins may run in
    org scope (their own bucket).

    **Scope:** the limiter is in-memory / single-process (like the foundation
    login throttle) — a single-process fairness guardrail. The durable,
    cross-replica distributed quota is W6 ("distributed rate limiting"), not this
    PR; a multi-replica deployment needs that to fully close W7 fairness.

    Raises ``_RunProjectRequiredError`` (-> 409) when a non-admin omits the
    project, and ``_QuotaExceededError`` (-> 429) when the rate is exceeded.
    No-op in single-org mode or when the rate is unset.
    """
    ctx = _multi_ctx(request)
    if ctx is None:
        return
    if ctx.project_id is None and not ctx.is_org_admin:
        raise _RunProjectRequiredError()
    throttle = getattr(getattr(request.app, "state", None), "run_throttle", None)
    if throttle is None:
        return
    if not throttle.allow((ctx.org_id, ctx.project_id)):
        raise _QuotaExceededError("project run-rate quota exceeded")


def _require_resource_write(request: Request) -> None:
    """Enforce RBAC write on a tenant-scoped resource (multi-tenant only).

    Raises ``PermissionDeniedError`` (403) / ``TenantHiddenError`` (404), mapped
    to HTTP by the app exception handlers. No-op in single-org mode (scope is
    organizational there, not a boundary).
    """
    ctx = getattr(request.state, "context", None)
    if ctx is None or ctx.tenancy_mode != "multi":
        return
    from sagewai.admin.authz import Resource, require

    require("resource:write", ctx, on=Resource(ctx.org_id, ctx.project_id))


def _multi_ctx(request: Request):
    """The RequestContext if running in multi-tenant mode, else None."""
    ctx = getattr(request.state, "context", None)
    return ctx if (ctx is not None and ctx.tenancy_mode == "multi") else None


# ── OTel metrics (module-level so route handlers can use them) ────

_otel_meter = None  # set by _init_otel if OTel is available
_otel_counters: dict[str, Any] = {}
_otel_histograms: dict[str, Any] = {}


def otel_count(name: str, value: int = 1, **labels: str) -> None:
    """Increment an OTel counter (no-op if OTel not installed)."""
    c = _otel_counters.get(name)
    if c:
        c.add(value, labels)


def otel_record(name: str, value: float, **labels: str) -> None:
    """Record an OTel histogram observation (no-op if OTel not installed)."""
    h = _otel_histograms.get(name)
    if h:
        h.record(value, labels)


def _classify_route(path: str) -> str:
    """Classify a request path into a business category."""
    if path.startswith("/api/v1/setup"):
        return "setup"
    if path.startswith("/api/v1/auth"):
        return "auth"
    if path.startswith("/api/v1/organization"):
        return "org"
    if path.startswith("/api/v1/project"):
        return "project"
    if path.startswith("/api/v1/provider"):
        return "provider"
    if path.startswith("/playground"):
        return "playground"
    if path.startswith("/workflow"):
        return "workflow"
    if path.startswith("/api/v1/agents/template"):
        return "template"
    if path.startswith("/admin"):
        return "admin"
    if path.startswith("/api/v1/health"):
        return "health"
    return "other"


def _init_otel(app: FastAPI, version: str) -> None:
    """Set up OpenTelemetry if packages are installed."""
    global _otel_meter
    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        import time as _time
        from starlette.middleware.base import BaseHTTPMiddleware

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        resource = Resource.create({
            "service.name": "sagewai-admin",
            "service.version": version,
            "service.namespace": "sagewai",
        })

        # Traces
        tp = TracerProvider(resource=resource)
        tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")))
        trace.set_tracer_provider(tp)

        # Metrics
        # Use CUMULATIVE temporality so counters are compatible with
        # Prometheus remote-write (VictoriaMetrics expects cumulative).
        from opentelemetry.sdk.metrics.export import AggregationTemporality
        metric_exporter = OTLPMetricExporter(
            endpoint=f"{endpoint}/v1/metrics",
            preferred_temporality={
                # All instrument types → cumulative
            },
        )
        reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=10_000,
        )
        mp = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(mp)

        # ── Custom business metrics ──────────────────────────────
        _otel_meter = metrics.get_meter("sagewai.admin", version)

        _otel_counters["agent.runs"] = _otel_meter.create_counter(
            "sagewai.agent.runs", description="Total agent runs", unit="{run}")
        _otel_counters["agent.run.errors"] = _otel_meter.create_counter(
            "sagewai.agent.run.errors", description="Failed agent runs", unit="{error}")
        _otel_counters["auth.logins"] = _otel_meter.create_counter(
            "sagewai.auth.logins", description="Login attempts", unit="{attempt}")
        _otel_counters["setup.completions"] = _otel_meter.create_counter(
            "sagewai.setup.completions", description="Setup wizard completions", unit="{completion}")
        _otel_counters["provider.tests"] = _otel_meter.create_counter(
            "sagewai.provider.tests", description="Provider connection tests", unit="{test}")
        _otel_counters["agent.created"] = _otel_meter.create_counter(
            "sagewai.agent.created", description="Agents created", unit="{agent}")
        _otel_counters["llm.tokens"] = _otel_meter.create_counter(
            "sagewai.llm.tokens", description="LLM tokens consumed", unit="{token}")

        _otel_histograms["agent.run.duration"] = _otel_meter.create_histogram(
            "sagewai.agent.run.duration", description="Agent run duration", unit="s")
        _otel_histograms["provider.test.latency"] = _otel_meter.create_histogram(
            "sagewai.provider.test.latency", description="Provider test latency", unit="ms")

        # Logs → OTel collector
        lp = LoggerProvider(resource=resource)
        lp.add_log_record_processor(BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=f"{endpoint}/v1/logs")
        ))
        handler = LoggingHandler(level=logging.INFO, logger_provider=lp)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
            lg = logging.getLogger(name)
            lg.addHandler(handler)
            lg.setLevel(logging.INFO)

        _app_log = logging.getLogger("sagewai.admin")
        _app_log.setLevel(logging.INFO)

        # ── Structured request logging middleware ────────────────
        class _ReqLog(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Any) -> Any:
                t0 = _time.monotonic()
                response = await call_next(request)
                dt = (_time.monotonic() - t0) * 1000
                path = request.url.path
                category = _classify_route(path)
                # Skip health check noise
                if category == "health":
                    return response
                _app_log.info(
                    "%s %s %d %.1fms",
                    request.method, path, response.status_code, dt,
                    extra={
                        "event": "http.request",
                        "http.method": request.method,
                        "http.route": path,
                        "http.status_code": response.status_code,
                        "http.duration_ms": round(dt, 1),
                        "sagewai.category": category,
                        "sagewai.project_id": _project_scope(request) or "global",
                    },
                )
                return response

        app.add_middleware(_ReqLog)
        FastAPIInstrumentor.instrument_app(app)
        logger.info("OpenTelemetry → %s", endpoint)
    except ImportError:
        logger.info("OpenTelemetry: not installed")
    except Exception as exc:
        logger.warning("OpenTelemetry init failed: %s", exc)
