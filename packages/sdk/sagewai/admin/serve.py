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

    app = FastAPI(title="Sagewai Admin", version=version)

    # CORS — allow admin dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Existing routers
    state = AdminState()
    analytics = AnalyticsStore()
    app.include_router(create_admin_router(state), prefix="/admin")
    app.include_router(
        create_analytics_router(analytics), prefix="/api/v1/analytics"
    )
    app.include_router(
        create_analytics_router(analytics), prefix="/analytics"
    )

    # Override /admin/agents to also include playground-created agents
    @app.get("/admin/agents", include_in_schema=False)
    async def admin_agents_merged() -> JSONResponse:
        """Merge SDK-registered agents with playground-created agents."""
        playground_agents = sf.list_agents()
        result = [
            {
                "name": a.get("name", ""),
                "model": a.get("model", ""),
                "strategy": a.get("strategy", ""),
                "status": "idle",
                "total_runs": 0,
                "source": "playground",
            }
            for a in playground_agents
        ]
        return JSONResponse(result)

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
    async def list_providers() -> JSONResponse:
        return JSONResponse(sf.list_providers())

    @app.post("/api/v1/providers")
    async def upsert_provider(request: Request) -> JSONResponse:
        body = await request.json()
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
        agent = sf.create_agent(body)
        logger.info("Agent created: %s model=%s strategy=%s",
                     body["name"], body.get("model", ""), body.get("strategy", ""),
                     extra={"event": "agent.created", "agent_name": body["name"],
                            "model": body.get("model", ""), "strategy": body.get("strategy", "")})
        otel_count("agent.created", agent_name=body["name"])
        return JSONResponse(agent, status_code=201)

    @app.get("/playground/agents")
    async def playground_agents() -> JSONResponse:
        agents = sf.list_agents()
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

        import time as _run_time
        body = await request.json()
        agent_name = body.get("agent_name", "")
        message = body.get("message", "")
        agent_spec = sf.get_agent(agent_name)
        _run_t0 = _run_time.monotonic()

        logger.info("Agent run started: agent=%s model=%s",
                     agent_name, (agent_spec or {}).get("model", ""),
                     extra={"event": "agent.run.started", "agent_name": agent_name,
                            "model": (agent_spec or {}).get("model", "")})
        otel_count("agent.runs", agent_name=agent_name)

        async def _generate():
            model = (agent_spec or {}).get("model", "")
            system_prompt = (agent_spec or {}).get("system_prompt", "")

            # Try to call the LLM via litellm
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

                full_output = ""
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
                yield f"event: run_finished\ndata: {json.dumps({'output': full_output, 'status': 'completed'})}\n\n"

            except ImportError:
                msg = "litellm is not installed. Run: uv pip install litellm"
                logger.error("Agent run failed: litellm not installed",
                             extra={"event": "agent.run.error", "agent_name": agent_name, "error": msg})
                otel_count("agent.run.errors", agent_name=agent_name, error="import")
                yield f"event: text_message_content\ndata: {json.dumps({'delta': msg})}\n\n"
                yield f"event: run_finished\ndata: {json.dumps({'output': msg, 'status': 'error'})}\n\n"

            except Exception as exc:
                error_msg = str(exc)
                logger.error("Agent run failed: agent=%s error=%s", agent_name, error_msg[:200],
                             extra={"event": "agent.run.error", "agent_name": agent_name,
                                    "model": model, "error": error_msg[:200]})
                otel_count("agent.run.errors", agent_name=agent_name, error="runtime")
                if "api_key" in error_msg.lower() or "authentication" in error_msg.lower():
                    guidance = (
                        f"No API key configured for model '{model}'. "
                        f"Go to System → AI Models to add your API key, "
                        f"or set the environment variable (e.g., OPENAI_API_KEY)."
                    )
                else:
                    guidance = f"Error running agent: {error_msg}"
                yield f"event: text_message_content\ndata: {json.dumps({'delta': guidance})}\n\n"
                yield f"event: run_finished\ndata: {json.dumps({'output': guidance, 'status': 'error'})}\n\n"

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
    async def export_prompts() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("prompt_logs", []))

    @app.get("/api/v1/prompts/examples")
    async def list_prompt_examples() -> JSONResponse:
        data = sf._read()
        examples = [l for l in data.get("prompt_logs", []) if l.get("is_example")]
        return JSONResponse(examples)

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
        return JSONResponse({
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "workers": 0,
        })

    @app.get("/workflows/workers")
    async def workflow_workers() -> JSONResponse:
        return JSONResponse([])

    @app.post("/workflows/run")
    async def workflow_run(request: Request) -> JSONResponse:
        body = await request.json()
        import secrets as _sec
        run_id = f"wf-{_sec.token_hex(6)}"
        return JSONResponse({"run_id": run_id, "status": "queued"})

    @app.get("/workflows/runs/{run_id}")
    async def workflow_run_detail(run_id: str) -> JSONResponse:
        return JSONResponse({
            "run_id": run_id, "status": "completed",
            "steps": [], "started_at": None, "finished_at": None,
        })

    @app.post("/workflows/runs/{run_id}/cancel")
    async def workflow_cancel(run_id: str) -> JSONResponse:
        return JSONResponse({"status": "cancelled"})

    @app.post("/workflows/runs/{run_id}/approve")
    async def workflow_approve(run_id: str, request: Request) -> JSONResponse:
        return JSONResponse({"status": "approved"})

    @app.post("/workflows/runs/{run_id}/reject")
    async def workflow_reject(run_id: str, request: Request) -> JSONResponse:
        return JSONResponse({"status": "rejected"})

    @app.get("/workflows/history")
    async def workflow_history() -> JSONResponse:
        return JSONResponse([])

    @app.get("/workflows/history/{run_id}")
    async def workflow_history_detail(run_id: str) -> JSONResponse:
        return JSONResponse({
            "run_id": run_id, "status": "completed",
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
        return JSONResponse({"valid": True, "errors": []})

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

    # ── Fleet ────────────────────────────────────────────────────

    @app.get("/api/v1/fleet/workers")
    async def list_fleet_workers() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("fleet_workers", []))

    @app.get("/api/v1/fleet/workers/{worker_id}")
    async def get_fleet_worker(worker_id: str) -> JSONResponse:
        data = sf._read()
        worker = next((w for w in data.get("fleet_workers", []) if w.get("id") == worker_id), None)
        if worker:
            return JSONResponse(worker)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.post("/api/v1/fleet/workers/{worker_id}/approve")
    async def approve_fleet_worker(worker_id: str) -> JSONResponse:
        return JSONResponse({"status": "approved", "worker_id": worker_id})

    @app.post("/api/v1/fleet/workers/{worker_id}/reject")
    async def reject_fleet_worker(worker_id: str) -> JSONResponse:
        return JSONResponse({"status": "rejected", "worker_id": worker_id})

    @app.post("/api/v1/fleet/workers/{worker_id}/revoke")
    async def revoke_fleet_worker(worker_id: str) -> JSONResponse:
        return JSONResponse({"status": "revoked", "worker_id": worker_id})

    @app.get("/api/v1/fleet/enrollment-keys")
    async def list_fleet_enrollment_keys() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("fleet_enrollment_keys", []))

    @app.post("/api/v1/fleet/enrollment-keys")
    async def create_fleet_enrollment_key(request: Request) -> JSONResponse:
        import secrets as _sec
        body = await request.json()
        key = {
            "id": f"ek-{_sec.token_hex(6)}",
            "key": f"swek_{_sec.token_urlsafe(24)}",
            "name": body.get("name", ""),
            "pool": body.get("pool", "default"),
            "max_uses": body.get("max_uses", 1),
            "uses": 0,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "revoked": False,
        }
        data = sf._read()
        data.setdefault("fleet_enrollment_keys", []).append(key)
        sf._write(data)
        return JSONResponse(key, status_code=201)

    @app.delete("/api/v1/fleet/enrollment-keys/{key_id}")
    async def revoke_fleet_enrollment_key(key_id: str) -> JSONResponse:
        data = sf._read()
        for k in data.get("fleet_enrollment_keys", []):
            if k.get("id") == key_id:
                k["revoked"] = True
                sf._write(data)
                return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

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
        return JSONResponse({"sent": True})

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
