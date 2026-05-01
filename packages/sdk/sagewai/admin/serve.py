# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
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
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from sagewai.admin.state_file import AdminStateFile

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
    "mcp_servers": [
        {"id": "filesystem", "name": "Filesystem", "description": "Read/write local files via MCP."},
        {"id": "github", "name": "GitHub", "description": "Interact with GitHub repos, issues, PRs."},
        {"id": "postgres", "name": "PostgreSQL", "description": "Query Postgres databases."},
        {"id": "slack", "name": "Slack", "description": "Send and read Slack messages."},
    ],
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
                "tools": [], "mcp_servers": ["filesystem", "github"],
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


def create_admin_serve_app(
    sf: AdminStateFile,
    *,
    version: str = "0.1.1",
) -> FastAPI:
    """Create the complete admin API server.

    Parameters
    ----------
    sf:
        The state-file store instance.
    version:
        SDK version string (injected by the CLI).
    """
    from sagewai.admin import create_admin_router
    from sagewai.admin.analytics import (
        AnalyticsStore,
        create_analytics_router,
    )
    from sagewai.admin.state import AdminState
    from sagewai.autopilot.controller.driver import MissionDriver
    from sagewai.autopilot.controller.runner import SchedulerRunner
    from sagewai.autopilot.controller.scheduler import MissionScheduler

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
        try:
            yield
        finally:
            await runner.stop()

    app = FastAPI(title="Sagewai Admin", version=version, lifespan=lifespan)

    # CORS — allow admin dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    state = AdminState()
    analytics = AnalyticsStore()
    _register_optional_backends(sf)

    # Override /admin/agents and /admin/runs BEFORE include_router so
    # these direct app routes match first (Starlette matches routes in
    # registration order — whichever is added first wins). They merge
    # registry/playground agents and read agent runs from the file store
    # so the admin UI sees everything, not just SDK-registered state.
    @app.get("/admin/agents", include_in_schema=False)
    async def admin_agents_merged(request: Request) -> JSONResponse:
        """List every visible agent — playground specs from the file store."""
        pid = _project_id(request)
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
        pid = _project_id(request)
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
    async def admin_run_detail(run_id: str) -> JSONResponse:
        """Return full detail for a single agent run from the file store."""
        r = sf.get_agent_run(run_id)
        if r is None:
            return JSONResponse({"detail": f"Run '{run_id}' not found"}, status_code=404)
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

    app.include_router(create_autopilot_router(sf), prefix="/api/v1")

    # Sandbox config routes (Plan 3b-i)
    from sagewai.admin import sandbox_routes

    sandbox_routes.register(app, sf)

    # Sealed environment routes (Sealed-i)
    from sagewai.admin import sealed_routes  # noqa: E402

    sealed_routes.register(app, sf)

    # Plan ART — artifact destination admin routes
    from sagewai.admin import artifact_destination_routes  # noqa: E402

    artifact_destination_routes.register(app)

    # Sealed-v directive admin routes (in-memory; postgres-backed
    # approvals/evaluations are wired alongside revocation_routes below
    # when a database URL is configured)
    from sagewai.admin import directive_routes  # noqa: E402

    directive_routes.register(app, sf)

    # Sealed revocation routes (Sealed-iii.A) — requires Postgres
    _db_url = os.environ.get("SAGEWAI_DATABASE_URL")
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
    # Backend implementation lives in sagewai.harness; mounting its admin
    # router here makes apps/admin/app/harness/* pages functional. Uses
    # process-local InMemoryHarnessStore — data does not survive admin
    # restarts. PostgresHarnessStore (sagewai.harness.postgres_store) is the
    # production path; can be wired conditionally on SAGEWAI_DATABASE_URL
    # following the Sealed-iii.A pattern above when needed.
    from sagewai.harness import (
        HarnessConfig,
        InMemoryHarnessStore,
        RequestClassifier,
    )
    from sagewai.harness.admin_api import create_harness_admin_router

    _harness_store = InMemoryHarnessStore()
    _harness_classifier = RequestClassifier()
    _harness_config = HarnessConfig()
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
        logger.info("Setup completed for org=%s", result.get("org_slug"),
                     extra={"event": "setup.completed", "org_slug": result.get("org_slug", "")})
        otel_count("setup.completions")
        return JSONResponse(result)

    # ── Auth ─────────────────────────────────────────────────────

    @app.post("/api/v1/auth/login")
    async def auth_login(request: Request) -> JSONResponse:
        body = await request.json()
        result = sf.validate_login(
            body.get("email", ""), body.get("password", "")
        )
        if not result:
            logger.warning("Login failed for email=%s", body.get("email", ""),
                           extra={"event": "auth.login.failed", "email": body.get("email", "")})
            otel_count("auth.logins", status="failed")
            return JSONResponse(
                {"detail": "Invalid email or password"}, status_code=401
            )
        logger.info("Login success for email=%s", result["user"]["email"],
                     extra={"event": "auth.login.success", "email": result["user"]["email"]})
        otel_count("auth.logins", status="success")
        resp = JSONResponse(result)
        resp.set_cookie(
            key="sagewai_auth", value=result["access_token"],
            httponly=True, samesite="lax", path="/",
        )
        return resp

    @app.post("/api/v1/auth/refresh")
    async def auth_refresh(request: Request) -> JSONResponse:
        cookie = request.cookies.get("sagewai_auth")
        if not cookie:
            return JSONResponse({"detail": "No session"}, status_code=401)
        result = sf.refresh_token(cookie)
        if not result:
            return JSONResponse({"detail": "Invalid session"}, status_code=401)
        resp = JSONResponse(result)
        resp.set_cookie(
            key="sagewai_auth", value=result["access_token"],
            httponly=True, samesite="lax", path="/",
        )
        return resp

    @app.post("/api/v1/auth/logout")
    async def auth_logout() -> JSONResponse:
        resp = JSONResponse({"status": "ok"})
        resp.delete_cookie("sagewai_auth", path="/")
        return resp

    @app.get("/api/v1/auth/me")
    async def auth_me(request: Request) -> JSONResponse:
        token = _extract_token(request)
        if not token:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        user = sf.get_user_by_token(token)
        if not user:
            return JSONResponse({"detail": "Invalid token"}, status_code=401)
        return JSONResponse(user)

    # ── Organization ─────────────────────────────────────────────

    @app.get("/api/v1/organization")
    async def get_org() -> JSONResponse:
        return JSONResponse(sf.get_org())

    @app.patch("/api/v1/organization")
    async def update_org(request: Request) -> JSONResponse:
        body = await request.json()
        return JSONResponse(sf.update_org(body))

    # ── Projects ─────────────────────────────────────────────────

    @app.get("/api/v1/projects")
    async def list_projects() -> JSONResponse:
        return JSONResponse(sf.list_projects())

    @app.post("/api/v1/projects")
    async def create_project(request: Request) -> JSONResponse:
        body = await request.json()
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
    async def get_project(slug: str) -> JSONResponse:
        for p in sf.list_projects():
            if p["slug"] == slug:
                return JSONResponse(p)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.patch("/api/v1/projects/{slug}")
    async def update_project(slug: str, request: Request) -> JSONResponse:
        body = await request.json()
        result = sf.update_project(slug, body)
        if result is None:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return JSONResponse(result)

    @app.delete("/api/v1/projects/{slug}")
    async def delete_project(slug: str) -> JSONResponse:
        try:
            if sf.delete_project(slug):
                return JSONResponse({"status": "ok"})
            return JSONResponse({"detail": "Not found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)

    # ── Providers ────────────────────────────────────────────────

    @app.get("/api/v1/providers")
    async def list_providers(request: Request) -> JSONResponse:
        pid = _project_id(request)
        return JSONResponse(sf.list_providers(project_id=pid))

    @app.post("/api/v1/providers")
    async def upsert_provider(request: Request) -> JSONResponse:
        body = await request.json()
        pid = _project_id(request)
        if pid:
            body["project_id"] = pid
        result = sf.upsert_provider(body)
        logger.info("Provider configured: %s", body.get("provider_name", ""),
                     extra={"event": "provider.configured", "provider": body.get("provider_name", "")})
        return JSONResponse({"id": result.get("id", "")})

    @app.post("/api/v1/providers/{provider_id}/test")
    async def test_provider(provider_id: str) -> JSONResponse:
        from sagewai.admin.provider_probes import test_cloud_provider

        providers = sf.list_providers()
        provider = next(
            (p for p in providers if p.get("id") == provider_id or p.get("provider_name") == provider_id),
            None,
        )
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
    async def delete_provider(provider_id: str) -> JSONResponse:
        if sf.delete_provider(provider_id):
            return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

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
    async def playground_models() -> JSONResponse:
        from sagewai.admin.provider_probes import aggregate_available_models
        providers = sf.list_providers()
        models = await aggregate_available_models(providers)
        return JSONResponse(models)

    @app.get("/playground/strategies")
    async def playground_strategies() -> JSONResponse:
        return JSONResponse([s["id"] for s in _STRATEGIES])

    @app.get("/playground/capabilities")
    async def playground_capabilities() -> JSONResponse:
        return JSONResponse(_CAPABILITIES)

    @app.get("/playground/presets")
    async def playground_presets() -> JSONResponse:
        return JSONResponse(_PRESETS)

    @app.post("/playground/agent")
    async def create_playground_agent(request: Request) -> JSONResponse:
        """Create or update a playground agent from a spec."""
        body = await request.json()
        if not body.get("name"):
            return JSONResponse({"detail": "Agent name is required"}, status_code=422)
        pid = _project_id(request)
        agent = sf.create_agent(body, project_id=pid)
        logger.info("Agent created: %s model=%s strategy=%s",
                     body["name"], body.get("model", ""), body.get("strategy", ""),
                     extra={"event": "agent.created", "agent_name": body["name"],
                            "model": body.get("model", ""), "strategy": body.get("strategy", "")})
        otel_count("agent.created", agent_name=body["name"])
        return JSONResponse(agent, status_code=201)

    @app.get("/playground/agents")
    async def playground_agents(request: Request) -> JSONResponse:
        pid = _project_id(request)
        agents = sf.list_agents(project_id=pid)
        return JSONResponse(agents)

    @app.get("/playground/agents/{name}")
    async def playground_agent_detail(name: str) -> JSONResponse:
        agent = sf.get_agent(name)
        if not agent:
            return JSONResponse({"detail": "Agent not found"}, status_code=404)
        return JSONResponse(agent)

    @app.get("/playground/agents/{name}/debug")
    async def playground_agent_debug(name: str) -> JSONResponse:
        agent = sf.get_agent(name)
        if not agent:
            return JSONResponse({"detail": "Agent not found"}, status_code=404)
        return JSONResponse(agent)

    @app.delete("/playground/agents/{name}")
    async def delete_playground_agent(name: str) -> JSONResponse:
        if sf.delete_agent(name):
            return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.post("/playground/agents/{name}/rename")
    async def rename_playground_agent(name: str, request: Request) -> JSONResponse:
        body = await request.json()
        new_name = body.get("new_name", "")
        if not new_name:
            return JSONResponse({"detail": "new_name required"}, status_code=422)
        result = sf.rename_agent(name, new_name)
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
        pid = _project_id(request)
        agent_spec = sf.get_agent(agent_name)
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

                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    stream=True,
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
                if "api_key" in error_msg.lower() or "authentication" in error_msg.lower():
                    full_output = (
                        f"No API key configured for model '{model}'. "
                        f"Go to System → AI Models to add your API key, "
                        f"or set the environment variable (e.g., OPENAI_API_KEY)."
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
                        "project_id": pid,
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
    async def list_prompt_logs() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("prompt_logs", []))

    @app.post("/api/v1/prompts/logs")
    async def save_prompt_log(request: Request) -> JSONResponse:
        body = await request.json()
        pid = _project_id(request)
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
            "project_id": pid,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        data = sf._read()
        data.setdefault("prompt_logs", []).append(entry)
        sf._write(data)
        return JSONResponse({"log_id": log_id}, status_code=201)

    @app.get("/api/v1/prompts/logs/{log_id}")
    async def get_prompt_log(log_id: str) -> JSONResponse:
        data = sf._read()
        for log in data.get("prompt_logs", []):
            if log.get("log_id") == log_id:
                return JSONResponse(log)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.patch("/api/v1/prompts/logs/{log_id}")
    async def update_prompt_log(log_id: str, request: Request) -> JSONResponse:
        body = await request.json()
        data = sf._read()
        for log in data.get("prompt_logs", []):
            if log.get("log_id") == log_id:
                for k in ("tags", "is_example", "output_text"):
                    if k in body:
                        log[k] = body[k]
                sf._write(data)
                return JSONResponse(log)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/prompts/logs/{log_id}")
    async def delete_prompt_log(log_id: str) -> JSONResponse:
        data = sf._read()
        logs = data.get("prompt_logs", [])
        data["prompt_logs"] = [l for l in logs if l.get("log_id") != log_id]
        sf._write(data)
        return JSONResponse({"status": "ok"})

    @app.post("/api/v1/prompts/replay")
    async def replay_prompt(request: Request) -> JSONResponse:
        return JSONResponse({"detail": "Replay requires a running agent"}, status_code=501)

    @app.get("/api/v1/prompts/export")
    async def export_prompts(request: Request) -> JSONResponse:
        pid = _project_id(request)
        data = sf._read()
        logs = data.get("prompt_logs", [])
        if pid:
            logs = [l for l in logs if l.get("project_id") in (pid, None)]
        return JSONResponse(logs)

    @app.get("/api/v1/prompts/examples")
    async def list_prompt_examples(request: Request) -> JSONResponse:
        pid = _project_id(request)
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

        pid = _project_id(request) or request.query_params.get("project_id")
        fmt = request.query_params.get("format", "alpaca")
        min_quality = int(request.query_params.get("min_quality", "0"))
        agent_filter = request.query_params.get("agent_name")

        data = sf._read()
        samples = [l for l in data.get("prompt_logs", []) if l.get("is_example")]

        # Filter
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
                agent = sf.get_agent(s.get("agent_name", ""))
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
        pid = _project_id(request)
        data = sf._read()
        all_logs = data.get("prompt_logs", [])
        examples = [l for l in all_logs if l.get("is_example")]
        if pid:
            examples = [e for e in examples if e.get("project_id") in (pid, None)]

        # Stats by agent
        by_agent: dict[str, int] = {}
        for e in examples:
            agent = e.get("agent_name", "unknown")
            by_agent[agent] = by_agent.get(agent, 0) + 1

        return JSONResponse({
            "total_samples": len(examples),
            "total_logs": len(all_logs),
            "by_agent": by_agent,
            "formats_available": ["alpaca", "sharegpt", "raw"],
            "export_url": "/api/v1/training/export",
        })

    @app.post("/api/v1/training/samples/{log_id}/quality")
    async def rate_training_sample(log_id: str, request: Request) -> JSONResponse:
        """Rate a training sample quality (1-5)."""
        body = await request.json()
        quality = body.get("quality", 3)
        data = sf._read()
        for log in data.get("prompt_logs", []):
            if log.get("log_id") == log_id:
                log["quality"] = max(1, min(5, int(quality)))
                log["is_example"] = True  # rating implies it's a training sample
                sf._write(data)
                return JSONResponse({"status": "ok", "quality": log["quality"]})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.get("/strategies/list")
    async def strategies_list() -> JSONResponse:
        return JSONResponse(_STRATEGIES)

    @app.get("/strategies/detail")
    async def strategies_detail() -> JSONResponse:
        return JSONResponse(_STRATEGIES)

    # ── Model router ─────────────────────────────────────────────

    @app.get("/api/v1/model-router/models")
    async def model_router_models() -> JSONResponse:
        from sagewai.admin.provider_probes import aggregate_available_models
        providers = sf.list_providers()
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

    @app.get("/workflows/stats")
    async def workflow_stats() -> JSONResponse:
        data = sf._read()
        runs = data.get("workflow_runs", [])
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
        pid = _project_id(request)
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

                    # Call LLM via litellm
                    step_output = ""
                    step_status = "completed"
                    try:
                        import litellm
                        litellm.suppress_debug_info = True
                        messages = [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": current_input},
                        ]
                        response = await litellm.acompletion(
                            model=model, messages=messages, stream=True
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
                        sf.save_agent_run({
                            "run_id": step_run_id,
                            "agent_name": agent_name,
                            "model": model,
                            "status": step_status,
                            "input_text": current_input,
                            "output_text": step_output,
                            "total_tokens": step_tokens,
                            "started_at": step_started_at,
                            "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "run_type": "workflow_step",
                            "parent_workflow_run_id": run_id,
                            "tool_calls": [],
                            "project_id": pid,
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
                    "steps": steps, "project_id": pid,
                    "elapsed_seconds": elapsed,
                    "total_tokens": total_tokens,
                    "total_input_tokens": total_input_tokens,
                    "total_output_tokens": total_output_tokens,
                    "agents": agents_data,
                    "started_at": now,
                    "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "events": events_log,
                }
                data = sf._read()
                data.setdefault("workflow_runs", []).insert(0, run_record)
                data["workflow_runs"] = data["workflow_runs"][:100]
                sf._write(data)

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
    async def workflow_run_detail(run_id: str) -> JSONResponse:
        data = sf._read()
        for r in data.get("workflow_runs", []):
            if r.get("run_id") == run_id:
                return JSONResponse(r)
        return JSONResponse({
            "run_id": run_id, "status": "not_found",
            "steps": [], "started_at": None, "finished_at": None,
        })

    @app.get("/workflows/runs/{run_id}/events")
    async def workflow_run_events(run_id: str):
        """Stream stored events for a completed workflow run as SSE.

        Live events for an in-flight run arrive via the /workflows/run
        POST response. This endpoint is the replay-only path used by the
        history detail page to populate the Events tab for completed runs.
        """
        data = sf._read()
        target = None
        for r in data.get("workflow_runs", []):
            if r.get("run_id") == run_id:
                target = r
                break

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
    async def workflow_cancel(run_id: str) -> JSONResponse:
        data = sf._read()
        for r in data.get("workflow_runs", []):
            if r.get("run_id") == run_id:
                r["status"] = "cancelled"
                sf._write(data)
                return JSONResponse({"status": "cancelled"})
        return JSONResponse({"status": "not_found"}, status_code=404)

    @app.post("/workflows/runs/{run_id}/approve")
    async def workflow_approve(run_id: str, request: Request) -> JSONResponse:
        return JSONResponse({"status": "approved"})

    @app.post("/workflows/runs/{run_id}/reject")
    async def workflow_reject(run_id: str, request: Request) -> JSONResponse:
        return JSONResponse({"status": "rejected"})

    @app.get("/workflows/history")
    async def workflow_history(request: Request) -> JSONResponse:
        pid = _project_id(request)
        data = sf._read()
        runs = data.get("workflow_runs", [])
        if pid:
            runs = [r for r in runs if r.get("project_id") in (pid, None)]
        return JSONResponse(runs)

    @app.get("/workflows/history/{run_id}")
    async def workflow_history_detail(run_id: str) -> JSONResponse:
        data = sf._read()
        for r in data.get("workflow_runs", []):
            if r.get("run_id") == run_id:
                return JSONResponse(r)
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
        return JSONResponse({"new_run_id": f"wf-retry-{run_id}"})

    @app.delete("/workflows/dlq/{run_id}")
    async def workflow_dlq_discard(run_id: str) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.post("/workflows/dispatch")
    async def workflow_dispatch(request: Request) -> JSONResponse:
        body = await request.json()
        import secrets as _sec
        return JSONResponse({"run_id": f"wf-{_sec.token_hex(6)}", "is_new": True})

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
        data = sf._read()
        data.setdefault("saved_workflows", []).append(wf)
        sf._write(data)
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
    async def delete_saved_workflow(wf_id: str) -> JSONResponse:
        data = sf._read()
        wfs = data.get("saved_workflows", [])
        data["saved_workflows"] = [w for w in wfs if w.get("id") != wf_id]
        sf._write(data)
        return JSONResponse({"status": "ok"})

    # ── Budget limits ────────────────────────────────────────────

    @app.get("/api/v1/budget/limits")
    async def list_budget_limits() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("budget_limits", []))

    @app.post("/api/v1/budget/limits")
    async def create_budget_limit(request: Request) -> JSONResponse:
        body = await request.json()
        data = sf._read()
        limits = data.setdefault("budget_limits", [])
        body.setdefault("created_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
        # Upsert by agent_name
        agent = body.get("agent_name", "")
        data["budget_limits"] = [l for l in limits if l.get("agent_name") != agent]
        data["budget_limits"].append(body)
        sf._write(data)
        return JSONResponse(body, status_code=201)

    @app.put("/api/v1/budget/limits/{agent_name}")
    async def update_budget_limit(agent_name: str, request: Request) -> JSONResponse:
        body = await request.json()
        data = sf._read()
        for l in data.get("budget_limits", []):
            if l.get("agent_name") == agent_name:
                l.update(body)
                sf._write(data)
                return JSONResponse(l)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/budget/limits/{agent_name}")
    async def delete_budget_limit(agent_name: str) -> JSONResponse:
        data = sf._read()
        limits = data.get("budget_limits", [])
        data["budget_limits"] = [l for l in limits if l.get("agent_name") != agent_name]
        sf._write(data)
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
        body = await request.json()
        body["agent_name"] = agent_name
        data = sf._read()
        configs = data.setdefault("guardrail_configs", [])
        data["guardrail_configs"] = [c for c in configs if c.get("agent_name") != agent_name]
        data["guardrail_configs"].append(body)
        sf._write(data)
        return JSONResponse(body)

    @app.delete("/api/v1/guardrails/configs/{agent_name}/{guardrail_type}")
    async def delete_guardrail_config(agent_name: str, guardrail_type: str) -> JSONResponse:
        data = sf._read()
        configs = data.get("guardrail_configs", [])
        for c in configs:
            if c.get("agent_name") == agent_name:
                types = c.get("guardrails", [])
                c["guardrails"] = [g for g in types if g.get("type") != guardrail_type]
                sf._write(data)
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
    async def list_tokens() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("api_tokens", []))

    @app.post("/api/v1/tokens/")
    async def create_token(request: Request) -> JSONResponse:
        import secrets as _sec
        body = await request.json()
        token_value = f"sw_{_sec.token_urlsafe(32)}"
        entry = {
            "id": f"tok-{_sec.token_hex(6)}",
            "name": body.get("name", "Unnamed"),
            "token": token_value,
            "prefix": token_value[:12],
            "scopes": body.get("scopes", ["read"]),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "last_used": None,
            "revoked": False,
        }
        data = sf._read()
        data.setdefault("api_tokens", []).append(entry)
        sf._write(data)
        return JSONResponse(entry, status_code=201)

    @app.post("/api/v1/tokens/{token_id}/revoke")
    async def revoke_token(token_id: str) -> JSONResponse:
        data = sf._read()
        for t in data.get("api_tokens", []):
            if t.get("id") == token_id:
                t["revoked"] = True
                sf._write(data)
                return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/tokens/{token_id}")
    async def delete_token(token_id: str) -> JSONResponse:
        data = sf._read()
        tokens = data.get("api_tokens", [])
        data["api_tokens"] = [t for t in tokens if t.get("id") != token_id]
        sf._write(data)
        return JSONResponse({"status": "ok"})

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

    @app.post("/api/v1/fleet/register")
    async def fleet_register(request: Request) -> JSONResponse:
        """Worker self-registration."""
        body = await request.json()
        pid = _project_id(request)
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
            org_id=body.get("org_id", "default"),
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
            org_id=body.get("org_id", "default"),
            models_canonical=body.get("models", []),
            pool=body.get("pool", "default"),
            labels=body.get("labels"),
        )
        if task:
            return JSONResponse(task)
        return JSONResponse(None, status_code=204)

    @app.post("/api/v1/fleet/report")
    async def fleet_report(request: Request) -> JSONResponse:
        """Worker reports task completion."""
        body = await request.json()
        await fleet_dispatcher.report(
            worker_id=body.get("worker_id", ""),
            org_id=body.get("org_id", "default"),
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
    async def list_fleet_workers() -> JSONResponse:
        workers = await fleet_registry.list_workers(org_id="default")
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
    async def get_fleet_worker(worker_id: str) -> JSONResponse:
        w = await fleet_registry.get_worker(worker_id)
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
    async def approve_fleet_worker(worker_id: str) -> JSONResponse:
        w = await fleet_registry.approve_worker(worker_id, approved_by="admin")
        return JSONResponse({"status": w.approval_status.value, "worker_id": w.id})

    @app.post("/api/v1/fleet/workers/{worker_id}/reject")
    async def reject_fleet_worker(worker_id: str) -> JSONResponse:
        w = await fleet_registry.reject_worker(worker_id)
        return JSONResponse({"status": w.approval_status.value, "worker_id": w.id})

    @app.post("/api/v1/fleet/workers/{worker_id}/revoke")
    async def revoke_fleet_worker(worker_id: str) -> JSONResponse:
        w = await fleet_registry.revoke_worker(worker_id)
        return JSONResponse({"status": w.approval_status.value, "worker_id": w.id})

    @app.get("/api/v1/admin/fleet/workers/{worker_id}/pool-stats")
    async def get_worker_pool_stats(worker_id: str) -> JSONResponse:
        """Return the latest pool_stats snapshot from the worker's heartbeat cache.

        Returns 404 if the worker is unknown.
        Returns the snapshot (or null payload if worker reported nothing yet).
        """
        worker = await fleet_registry.get_worker(worker_id)
        if worker is None:
            return JSONResponse({"error": "worker not found"}, status_code=404)
        snap = await fleet_registry.get_pool_stats(worker_id)
        return JSONResponse(snap if snap else {"snapshot": None})

    @app.get("/api/v1/fleet/enrollment-keys")
    async def list_fleet_enrollment_keys() -> JSONResponse:
        keys = await fleet_registry.list_enrollment_keys(org_id="default")
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
        body = await request.json()
        key_record, raw_key = await fleet_registry.create_enrollment_key(
            org_id="default",
            name=body.get("name", ""),
            created_by="admin",
            max_uses=body.get("max_uses"),
            allowed_pools=body.get("pools", [body.get("pool", "default")]),
            allowed_models=body.get("models", []),
        )
        return JSONResponse({
            "id": key_record.id, "key": raw_key, "name": key_record.name,
            "max_uses": key_record.max_uses,
        }, status_code=201)

    @app.delete("/api/v1/fleet/enrollment-keys/{key_id}")
    async def revoke_fleet_enrollment_key(key_id: str) -> JSONResponse:
        await fleet_registry.revoke_enrollment_key(key_id)
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
        token = _extract_token(request)
        user = sf.get_user_by_token(token) if token else None
        if not user:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return JSONResponse(user)

    @app.patch("/api/v1/account/profile")
    async def update_profile(request: Request) -> JSONResponse:
        body = await request.json()
        data = sf._read()
        admin = data.get("admin", {})
        if body.get("display_name"):
            admin["name"] = body["display_name"]
            sf._write(data)
        return JSONResponse({
            "id": admin.get("id", ""),
            "email": admin.get("email", ""),
            "display_name": admin.get("name", ""),
            "avatar_url": None,
        })

    @app.post("/api/v1/account/password")
    async def change_password(request: Request) -> JSONResponse:
        body = await request.json()
        data = sf._read()
        admin = data.get("admin", {})
        from sagewai.admin.state_file import _verify_password, _hash_password
        if not _verify_password(body.get("current_password", ""),
                                admin.get("password_hash", ""), admin.get("password_salt", "")):
            return JSONResponse({"detail": "Current password is incorrect"}, status_code=400)
        new_hash, new_salt = _hash_password(body.get("new_password", ""))
        admin["password_hash"] = new_hash
        admin["password_salt"] = new_salt
        sf._write(data)
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
        data = sf._read()
        connectors = data.setdefault("connectors", [])
        data["connectors"] = [c for c in connectors if c.get("name") != name]
        data["connectors"].append(body)
        sf._write(data)
        return JSONResponse(body)

    @app.post("/api/v1/connectors/{name}/test")
    async def test_connector(name: str) -> JSONResponse:
        return JSONResponse({"connected": True, "name": name})

    @app.delete("/api/v1/connectors/{name}")
    async def delete_connector(name: str) -> JSONResponse:
        data = sf._read()
        connectors = data.get("connectors", [])
        data["connectors"] = [c for c in connectors if c.get("name") != name]
        sf._write(data)
        return JSONResponse({"status": "ok"})

    # ── Notifications ────────────────────────────────────────────

    @app.get("/api/v1/notifications/channels")
    async def list_notification_channels() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("notification_channels", []))

    @app.post("/api/v1/notifications/channels")
    async def save_notification_channel(request: Request) -> JSONResponse:
        import secrets as _sec
        body = await request.json()
        body.setdefault("id", f"ch-{_sec.token_hex(6)}")
        data = sf._read()
        channels = data.setdefault("notification_channels", [])
        data["notification_channels"] = [c for c in channels if c.get("id") != body["id"]]
        data["notification_channels"].append(body)
        sf._write(data)
        return JSONResponse(body, status_code=201)

    @app.delete("/api/v1/notifications/channels/{channel_id}")
    async def delete_notification_channel(channel_id: str) -> JSONResponse:
        data = sf._read()
        channels = data.get("notification_channels", [])
        data["notification_channels"] = [c for c in channels if c.get("id") != channel_id]
        sf._write(data)
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/notifications/triggers")
    async def list_notification_triggers() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("notification_triggers", []))

    @app.post("/api/v1/notifications/triggers")
    async def save_notification_trigger(request: Request) -> JSONResponse:
        import secrets as _sec
        body = await request.json()
        body.setdefault("id", f"tr-{_sec.token_hex(6)}")
        data = sf._read()
        triggers = data.setdefault("notification_triggers", [])
        data["notification_triggers"] = [t for t in triggers if t.get("id") != body["id"]]
        data["notification_triggers"].append(body)
        sf._write(data)
        return JSONResponse(body, status_code=201)

    @app.delete("/api/v1/notifications/triggers/{trigger_id}")
    async def delete_notification_trigger(trigger_id: str) -> JSONResponse:
        data = sf._read()
        triggers = data.get("notification_triggers", [])
        data["notification_triggers"] = [t for t in triggers if t.get("id") != trigger_id]
        sf._write(data)
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
        import secrets as _sec
        body = await request.json()
        body.setdefault("id", f"trig-{_sec.token_hex(6)}")
        data = sf._read()
        data.setdefault("triggers", []).append(body)
        sf._write(data)
        return JSONResponse(body, status_code=201)

    @app.delete("/api/v1/triggers/{trigger_id}")
    async def delete_trigger(trigger_id: str) -> JSONResponse:
        data = sf._read()
        triggers = data.get("triggers", [])
        data["triggers"] = [t for t in triggers if t.get("id") != trigger_id]
        sf._write(data)
        return JSONResponse({"status": "ok"})

    @app.patch("/api/v1/triggers/{trigger_id}/enable")
    async def enable_trigger(trigger_id: str) -> JSONResponse:
        data = sf._read()
        for t in data.get("triggers", []):
            if t.get("id") == trigger_id:
                t["enabled"] = True
                sf._write(data)
                return JSONResponse(t)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.patch("/api/v1/triggers/{trigger_id}/disable")
    async def disable_trigger(trigger_id: str) -> JSONResponse:
        data = sf._read()
        for t in data.get("triggers", []):
            if t.get("id") == trigger_id:
                t["enabled"] = False
                sf._write(data)
                return JSONResponse(t)
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
        return JSONResponse({"status": "ok", "chunks": 0})

    @app.get("/api/v1/memory/graph/stats")
    async def graph_stats() -> JSONResponse:
        return JSONResponse({"total_entities": 0, "total_relations": 0})

    @app.post("/api/v1/memory/graph/query")
    async def graph_query(request: Request) -> JSONResponse:
        return JSONResponse({"entities": [], "relations": []})

    @app.post("/api/v1/memory/graph/entity")
    async def create_graph_entity(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "entity": ""})

    @app.post("/api/v1/memory/graph/relation")
    async def create_graph_relation(request: Request) -> JSONResponse:
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
        import secrets as _sec
        body = await request.json()
        body.setdefault("id", f"ds-{_sec.token_hex(6)}")
        body.setdefault("created_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
        data = sf._read()
        data.setdefault("eval_datasets", []).append(body)
        sf._write(data)
        return JSONResponse(body, status_code=201)

    @app.get("/api/v1/eval/datasets/{dataset_id}")
    async def get_eval_dataset(dataset_id: str) -> JSONResponse:
        data = sf._read()
        ds = next((d for d in data.get("eval_datasets", []) if d.get("id") == dataset_id), None)
        if ds:
            return JSONResponse(ds)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/eval/datasets/{dataset_id}")
    async def delete_eval_dataset(dataset_id: str) -> JSONResponse:
        data = sf._read()
        datasets = data.get("eval_datasets", [])
        data["eval_datasets"] = [d for d in datasets if d.get("id") != dataset_id]
        sf._write(data)
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


def _extract_token(request: Request) -> str | None:
    """Get auth token from header or cookie."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get("sagewai_auth")


def _project_id(request: Request) -> str | None:
    """Extract project scope from X-Project-ID header or query param.

    Returns None for org-global scope (no filtering).
    """
    pid = request.headers.get("x-project-id") or request.query_params.get("project_id")
    return pid if pid else None


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
                        "sagewai.project_id": _project_id(request) or "global",
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
