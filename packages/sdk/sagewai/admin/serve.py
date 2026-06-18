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
from collections import defaultdict
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
from sagewai.fleet.dispatcher import NotTaskOwnerError
from sagewai.fleet.models import WorkerApprovalStatus

logger = logging.getLogger("sagewai.admin")

# Heartbeat interval for the global /workflow-events/stream SSE feed. Module-level
# so tests can shrink it. Keeps the stream open so the browser's EventSource does
# not reconnect in a tight loop (which floods the backend and exhausts the
# per-origin connection limit).
_WORKFLOW_EVENTS_HEARTBEAT_S = 15.0


async def _workflow_events_sse():
    """SSE body for ``/workflow-events/stream``.

    Stays open with periodic heartbeat comments so the browser's EventSource does
    not reconnect in a tight loop (no live event source is wired here yet — this
    just holds the connection). The server cancels this generator when the client
    disconnects, ending the loop.
    """
    import asyncio

    yield "data: {}\n\n"
    while True:
        await asyncio.sleep(_WORKFLOW_EVENTS_HEARTBEAT_S)
        yield ": keepalive\n\n"


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


# Last-resort / stub fallback impl names per intelligence component. When the
# resolved backend is one of these, the active (preferred) backend was missing
# its optional dependency, so the component reports ``available: false``.
_INTELLIGENCE_FALLBACK_IMPLS: dict[str, frozenset[str]] = {
    "embedder": frozenset({"HashEmbedder"}),
    "entity_extractor": frozenset({"LLMEntityExtractor"}),
    "relation_extractor": frozenset({"LLMRelationExtractor"}),
    "vision": frozenset({"StubVisionDescriber"}),
}


def _intelligence_status_payload() -> dict[str, Any]:
    """Introspect the runtime intelligence stack into a stable JSON shape.

    Returns ``{"components": [{"name", "impl", "available", "config"}, ...]}``.

    Every component is resolved through the :class:`ProviderRegistry` tiered
    fallback chains and wrapped in its own ``try``/``except`` so a missing
    optional dependency degrades to ``available: false`` (with the fallback
    impl name) rather than raising — the caller never 500s. Heavy optional
    intelligence deps are imported lazily here, never at module import time.
    """
    components: list[dict[str, Any]] = []

    def _add(
        name: str,
        *,
        impl: str,
        available: bool,
        config: dict[str, Any] | None = None,
    ) -> None:
        components.append(
            {
                "name": name,
                "impl": impl,
                "available": available,
                "config": config or {},
            }
        )

    def _is_fallback(name: str, impl: str) -> bool:
        return impl in _INTELLIGENCE_FALLBACK_IMPLS.get(name, frozenset())

    try:
        from sagewai.intelligence.config import IntelligenceConfig
        from sagewai.intelligence.registry import ProviderRegistry
    except Exception as exc:  # noqa: BLE001 — registry import is best-effort
        logger.debug("intelligence registry unavailable", exc_info=True)
        return {
            "components": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    cfg = IntelligenceConfig()

    # -- Embedder ------------------------------------------------------------
    try:
        emb = ProviderRegistry.get_embedder(cfg)
        impl = type(emb).__name__
        _add(
            "embedder",
            impl=impl,
            available=not _is_fallback("embedder", impl),
            config={
                "provider": cfg.embedding_provider,
                "dimension": getattr(emb, "dimension", None),
            },
        )
    except Exception as exc:  # noqa: BLE001
        _add("embedder", impl="unavailable", available=False,
             config={"error": f"{type(exc).__name__}: {exc}"})

    # -- Entity extractor (NER / GLiNER) -------------------------------------
    try:
        ner = ProviderRegistry.get_entity_extractor(cfg)
        impl = type(ner).__name__
        _add(
            "entity_extractor",
            impl=impl,
            available=not _is_fallback("entity_extractor", impl),
            config={
                "provider": cfg.extraction_provider,
                "model": cfg.extraction_model,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _add("entity_extractor", impl="unavailable", available=False,
             config={"error": f"{type(exc).__name__}: {exc}"})

    # -- Relation extractor --------------------------------------------------
    try:
        rel = ProviderRegistry.get_relation_extractor(cfg)
        impl = type(rel).__name__
        _add(
            "relation_extractor",
            impl=impl,
            available=not _is_fallback("relation_extractor", impl),
            config={"provider": cfg.extraction_provider},
        )
    except Exception as exc:  # noqa: BLE001
        _add("relation_extractor", impl="unavailable", available=False,
             config={"error": f"{type(exc).__name__}: {exc}"})

    # -- Language detection --------------------------------------------------
    try:
        from sagewai.intelligence.language.detector import LanguageDetector

        detector = LanguageDetector()
        _add(
            "language",
            impl="LanguageDetector",
            # The lingua backend is optional; .available reflects whether it
            # loaded (else detection always returns "en").
            available=bool(detector.available),
            config={"backend": "lingua" if detector.available else "fallback-en"},
        )
    except Exception as exc:  # noqa: BLE001
        _add("language", impl="unavailable", available=False,
             config={"error": f"{type(exc).__name__}: {exc}"})

    # -- Multimodal: vision --------------------------------------------------
    try:
        vis = ProviderRegistry.get_vision_describer(cfg)
        impl = type(vis).__name__
        _add(
            "vision",
            impl=impl,
            available=not _is_fallback("vision", impl),
            config={
                "provider": cfg.vision_provider,
                "model": cfg.vision_model,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _add("vision", impl="unavailable", available=False,
             config={"error": f"{type(exc).__name__}: {exc}"})

    # -- Multimodal: transcription -------------------------------------------
    try:
        tr = ProviderRegistry.get_transcriber(cfg)
        if tr is None:
            # Disabled by config or no backend available.
            _add(
                "transcriber",
                impl="none",
                available=False,
                config={"provider": cfg.transcription_provider},
            )
        else:
            _add(
                "transcriber",
                impl=type(tr).__name__,
                available=True,
                config={
                    "provider": cfg.transcription_provider,
                    "model": cfg.transcription_model,
                },
            )
    except Exception as exc:  # noqa: BLE001
        _add("transcriber", impl="unavailable", available=False,
             config={"error": f"{type(exc).__name__}: {exc}"})

    # -- Graph pipeline ------------------------------------------------------
    # Don't instantiate the (expensive) builder; report it as available when
    # both extractors resolved without erroring, reusing what we already know.
    try:
        by_name = {c["name"]: c for c in components}
        ner_ok = by_name.get("entity_extractor", {}).get("impl") not in (
            None,
            "unavailable",
        )
        rel_ok = by_name.get("relation_extractor", {}).get("impl") not in (
            None,
            "unavailable",
        )
        _add(
            "graph",
            impl="ConversationGraphBuilder",
            available=bool(ner_ok and rel_ok),
            config={
                "entity_extractor": by_name.get("entity_extractor", {}).get("impl"),
                "relation_extractor": by_name.get("relation_extractor", {}).get(
                    "impl"
                ),
            },
        )
    except Exception as exc:  # noqa: BLE001
        _add("graph", impl="unavailable", available=False,
             config={"error": f"{type(exc).__name__}: {exc}"})

    return {"components": components}


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


# Single-org project bucket for the in-process memory/context engines. In
# single-org mode the whole deployment is one project, so the vector/graph/
# context stores live under a stable id rather than the contextvar fallback.
_MEMORY_PROJECT_ID = "default"

# Cache key for the org-shared memory bucket (multi-tenant, project_id is None).
# Org-shared memory is distinct from any project's, so it gets its own namespace
# rather than colliding with the single-org "default" id.
_ORG_SHARED_MEMORY_ID = "__org_shared__"


class MemoryEngineResolver:
    """Per-project resolver for the admin memory + context engines.

    The 13 vector/graph/context routes ask this resolver for an engine bound to a
    concrete ``project_id`` (derived by the route from the session-validated
    ``RequestContext`` — never the ``X-Project-ID`` header). Each project gets its
    own engine instance, bound to that project, so reads/writes can never cross a
    tenant boundary: the vector/graph engines key their storage by ``project_id``,
    and the context engine's durable store filters every query by ``project_id``.

    Engines are built lazily and cached per ``project_id``. The factory callables
    are injectable so tests (and the durable-vs-in-memory selection) can vary the
    backend without changing the routes.

    Durability (multi-tenant):
    - **context** → :class:`PostgresContextStore` (Postgres or SQLite, durable):
      documents + chunks persist across restart, project-scoped by ``project_id``.
      The ANN vector index is an in-process :class:`InMemoryVectorStore` over a
      zero-dep hash embedder (rebuildable from the durable chunks; no API key).
    - **vector** → :class:`SqliteVecMemory` (file-durable) when sqlite-vec is
      available, else falls back to in-process :class:`VectorMemory` (FLAGGED:
      non-durable across restart).
    - **graph** → :class:`GraphMemory` (project-scoped, in-process; FLAGGED:
      non-durable — durable graph needs an optional heavy dep we deliberately do
      not require).

    Single-org mode resolves every request to the stable ``"default"`` project and
    uses the pure in-process engines (byte-identical to the prior behaviour).
    """

    def __init__(
        self,
        *,
        multi: bool,
        context_factory=None,
        vector_factory=None,
        graph_factory=None,
    ) -> None:
        self._multi = multi
        self._context_factory = context_factory or self._default_context_factory
        self._vector_factory = vector_factory or self._default_vector_factory
        self._graph_factory = graph_factory or self._default_graph_factory
        self._context: dict[str, Any] = {}
        self._vector: dict[str, Any] = {}
        self._graph: dict[str, Any] = {}

    # -- default backend factories ------------------------------------------

    @staticmethod
    def _default_context_factory(pid: str):
        # Durable document/chunk metadata via the process db engine (SQLite in
        # SAGEWAI_HOME or Postgres); the ANN index is in-process over the zero-dep
        # hash embedder (rebuildable, offline, no credentials). The single-org
        # path uses the in-memory metadata store for byte-identical behaviour.
        from sagewai.context.engine import ContextEngine
        from sagewai.context.pg_store import PostgresContextStore
        from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore

        if pid == _MEMORY_PROJECT_ID:
            metadata_store: Any = InMemoryMetadataStore()
        else:
            metadata_store = PostgresContextStore()
        return ContextEngine(
            metadata_store=metadata_store,
            vector_store=InMemoryVectorStore(),
            project_id=pid,
            embedder=None,
        )

    @staticmethod
    def _default_vector_factory(pid: str):
        # File-durable sqlite-vec when available; in-process VectorMemory otherwise
        # (FLAGGED non-durable). The single-org path stays on in-process
        # VectorMemory for byte-identical behaviour.
        from sagewai.memory.sqlite_vec import sqlite_vec_available
        from sagewai.memory.vector import VectorMemory

        if pid != _MEMORY_PROJECT_ID and sqlite_vec_available():
            from sagewai.memory.sqlite_vec import SqliteVecMemory

            return SqliteVecMemory(project_id=pid)
        return VectorMemory(project_id=pid)

    @staticmethod
    def _default_graph_factory(pid: str):
        # Project-scoped, in-process (FLAGGED non-durable across restart). A
        # durable graph backend needs an optional heavy dep (nebula) we do not
        # require; isolation is the guarantee here, durability is best-effort.
        from sagewai.memory.graph import GraphMemory

        return GraphMemory(project_id=pid)

    # -- resolution ---------------------------------------------------------

    def _key(self, project_id: str | None) -> str:
        """Normalise a scope to a cache/namespace key.

        Single-org always maps to ``"default"``. Multi-tenant uses the concrete
        project id, or the org-shared namespace when the context is org-scoped
        (``project_id is None``)."""
        if not self._multi:
            return _MEMORY_PROJECT_ID
        if project_id is None or project_id == SHARED_ONLY:
            return _ORG_SHARED_MEMORY_ID
        return project_id

    def context_for(self, project_id: str | None):
        key = self._key(project_id)
        engine = self._context.get(key)
        if engine is None:
            engine = self._context[key] = self._context_factory(key)
        return engine

    def vector_for(self, project_id: str | None):
        key = self._key(project_id)
        mem = self._vector.get(key)
        if mem is None:
            mem = self._vector[key] = self._vector_factory(key)
        return mem

    def graph_for(self, project_id: str | None):
        key = self._key(project_id)
        graph = self._graph.get(key)
        if graph is None:
            graph = self._graph[key] = self._graph_factory(key)
        return graph


def setup_memory_engines(app: FastAPI) -> None:
    """Attach the per-project memory + context engine resolver to ``app.state``.

    Wires ``app.state.memory_engines`` (a :class:`MemoryEngineResolver`), which the
    vector/graph/context routes use to obtain an engine bound to the request's
    project (multi-tenant) or the single ``"default"`` project (single-org). In
    multi mode the resolver hands out per-project, durable-backed engines so a
    read/write can never cross a tenant boundary.

    For backward compatibility (and the single-org route fast-path) it also binds
    the ``"default"`` engines on ``app.state.context_engine`` / ``vector_memory`` /
    ``graph_memory`` — byte-identical to the prior single-org behaviour, and the
    seam tests inject through.

    Idempotent: skips wiring already attached (injected DI / repeated calls).
    Imports happen inside the factories — the heavy/optional deps must not load at
    serve.py import time, and every engine has a zero-dep fallback so a missing
    optional dep can never crash startup. The admin lifespan calls this at startup;
    tests call it directly because ``httpx.ASGITransport`` does not run the
    lifespan.
    """
    from sagewai.admin.tenancy import is_multi_tenant

    if getattr(app.state, "memory_engines", None) is None:
        app.state.memory_engines = MemoryEngineResolver(multi=is_multi_tenant())

    resolver: MemoryEngineResolver = app.state.memory_engines

    # Bind the single-org "default" engines for the legacy app.state attributes
    # (single-org routes read them directly; tests inject through them). These are
    # the in-process zero-dep engines — unchanged from the prior behaviour.
    if getattr(app.state, "context_engine", None) is None:
        app.state.context_engine = resolver.context_for(_MEMORY_PROJECT_ID)
    else:
        resolver._context[_MEMORY_PROJECT_ID] = app.state.context_engine
    if getattr(app.state, "vector_memory", None) is None:
        app.state.vector_memory = resolver.vector_for(_MEMORY_PROJECT_ID)
    else:
        resolver._vector[_MEMORY_PROJECT_ID] = app.state.vector_memory
    if getattr(app.state, "graph_memory", None) is None:
        app.state.graph_memory = resolver.graph_for(_MEMORY_PROJECT_ID)
    else:
        resolver._graph[_MEMORY_PROJECT_ID] = app.state.graph_memory


# Backward-compatible alias: the original private name is imported by existing
# tests and the lifespan. Keep it pointing at the public function.
_setup_memory_engines = setup_memory_engines


async def _vector_doc_count(mem: Any) -> int:
    """Document count for a vector-memory backend, across backend shapes.

    In-process :class:`VectorMemory` exposes ``__len__`` (sync, project-scoped);
    the durable :class:`SqliteVecMemory` exposes an async, project-scoped
    ``count()`` (SQL ``COUNT(*)``). Either way the count is for the engine's bound
    project only."""
    if mem is None:
        return 0
    counter = getattr(mem, "count", None)
    if callable(counter):
        return await counter()
    try:
        return len(mem)
    except TypeError:
        return 0


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
    admin_resource_store: Any = None,
    api_token_store: Any = None,
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
    provider_store, agent_store, connection_store, admin_resource_store:
        Optional pre-built tenant resource stores (multi-tenant mode). When all
        are omitted the lifespan builds them from the process engine; inject for
        deterministic tests/wiring. Bundled on ``app.state.resource_stores``.
        ``admin_resource_store`` is the generic project-scoped control-plane store
        (budgets, guardrails, ...); see :class:`AdminResourceStore`.
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
        # Fail fast on an unsafe production deployment BEFORE any state is built.
        # No-op unless SAGEWAI_ENV=production (dev/test/single-org local unaffected).
        from sagewai.admin.prod_check import validate_production_config
        validate_production_config()

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
            or _rs.admin_resource is None
            or _rs.api_token is None
        ):
            _built = await build_resource_stores(identity_store)
            if _built is not None:
                app.state.resource_stores = type(_rs)(
                    provider=_rs.provider or _built.provider,
                    agent=_rs.agent or _built.agent,
                    connection=_rs.connection or _built.connection,
                    run=_rs.run or _built.run,
                    prompt_log=_rs.prompt_log or _built.prompt_log,
                    admin_resource=_rs.admin_resource or _built.admin_resource,
                    api_token=_rs.api_token or _built.api_token,
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

        # Attach the in-process memory + context engines the vector/graph/context
        # routes read. Zero-dep fallbacks inside, so this never crashes startup.
        _setup_memory_engines(app)

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
        run=run_store, prompt_log=prompt_log_store,
        admin_resource=admin_resource_store, api_token=api_token_store)

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

    # Multi-tenant: the AuthMiddleware needs the API-token store to authenticate a
    # bearer token before any context exists (find_by_hash → build_context). Build
    # it eagerly on the process engine when none was injected so the very first
    # request can authenticate a token (the lifespan-built bundle is too late for
    # the perimeter). Bundled on app.state.resource_stores too for the CRUD routes.
    if api_token_store is None and _is_multi_tenant():
        from sagewai.admin.api_token_store import ApiTokenStore
        api_token_store = ApiTokenStore()
        app.state.resource_stores.api_token = api_token_store

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
    app.add_middleware(
        AuthMiddleware, sf=sf, identity_store=identity_store,
        api_token_store=api_token_store,
    )

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

    # Generic admin-resource store (budgets, guardrails, ...): a write outside the
    # actor's scope is 403; a name collision in scope is 409. Mirrors how the
    # other tenant errors are mapped to HTTP.
    from sagewai.admin.admin_resource_store import (
        ResourceConflictError,
        ResourceWriteScopeError,
    )

    @app.exception_handler(ResourceWriteScopeError)
    async def _on_resource_write_scope(
        request: Request, exc: ResourceWriteScopeError
    ) -> JSONResponse:
        return JSONResponse({"detail": "Forbidden"}, status_code=403)

    @app.exception_handler(ResourceConflictError)
    async def _on_resource_conflict(
        request: Request, exc: ResourceConflictError
    ) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    # W7 per-project run-rate quota (multi-tenant): one tenant can't starve others.
    # The limiter is distributed (Postgres-backed, correct across worker
    # processes) in multi-tenant mode and in-memory single-process in single-org
    # (build_rate_limiter picks the backend; the tenant engine comes from the
    # identity store). W6 "distributed rate limiting" closed here.
    from sagewai.admin.tenancy import is_multi_tenant
    from sagewai.db.rate_limit import build_rate_limiter

    _tenant_engine = getattr(identity_store, "_engine", None)
    app.state.run_throttle = _ProjectRunThrottle(
        _run_quota_limit(),
        _run_quota_window(),
        build_rate_limiter(_tenant_engine, multi_tenant=is_multi_tenant()),
    )

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

    # Login brute-force throttle — distributed across workers in multi-tenant
    # mode (shared Postgres lockout), in-memory single-process in single-org.
    _login_throttle = _LoginThrottle(limiter=build_rate_limiter(_tenant_engine))

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

    @app.post("/admin/runs/{run_id}/cancel", include_in_schema=False)
    async def admin_run_cancel(run_id: str, request: Request) -> JSONResponse:
        """Cancel an agent run — mirror of :func:`workflow_cancel` for agent runs.

        Resolves the run **project-scoped** (cross-project or unknown id -> 404)
        and flips its status to ``cancelled``. Registered before the api.py admin
        router so it shadows that router's ``/runs/{id}/cancel`` (which targets
        in-memory ``run_controls`` and 501s when unconfigured — the dead path the
        Cancel button used to hit)."""
        store = _run_store(request)
        if store is not None:
            cancelled = await store.cancel_run_for(run_id, request.state.context)
        else:
            cancelled = sf.cancel_agent_run(run_id)
        if not cancelled:
            return JSONResponse(
                {"detail": f"Run '{run_id}' not found"}, status_code=404
            )
        await _emit_audit(request, "run.cancel", target_type="run", target_id=run_id)
        return JSONResponse({"status": "cancelled"})

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

    artifact_destination_routes.register(app, state_file=sf)

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
        if await _login_throttle.blocked_async(key):
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
                await _login_throttle.record_failure_async(key)
                otel_count("auth.logins", status="failed")
                return JSONResponse({"detail": "Invalid email or password"}, status_code=401)
            await _login_throttle.reset_async(key)
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
            await _login_throttle.record_failure_async(key)
            logger.warning("Login failed for email=%s", email,
                           extra={"event": "auth.login.failed", "email": email})
            otel_count("auth.logins", status="failed")
            emit_audit(sf, event_type="auth.login.failed", actor_label=email)
            return JSONResponse({"detail": "Invalid email or password"}, status_code=401)
        await _login_throttle.reset_async(key)
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
            await _emit_audit(
                request,
                "org.updated",
                target_type="org",
                target_id=ctx.org_id,
                metadata={"patched_keys": sorted(patch.keys())},
            )
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
            await _emit_audit(
                request,
                "project.updated",
                target_type="project",
                target_id=project["id"],
                metadata={"patched_keys": sorted(k for k in body.keys())},
            )
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

    async def _mcp_connections_for_request(request: Request) -> list[Any]:
        from sagewai.connections.bootstrap import build_connections_context
        from sagewai.connections.store_ops import store_list

        mctx = _multi_ctx(request)
        if mctx is not None:
            connection_store = _tenant_store_or_none(request, "connection")
            org_connections = list(await store_list(connection_store, None, protocol="mcp"))
            if mctx.project_id is None:
                return org_connections
            project_connections = list(
                await store_list(connection_store, mctx.project_id, protocol="mcp")
            )
            seen = set()
            merged = []
            for conn in project_connections + org_connections:
                key = (conn.protocol, conn.display_name)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(conn)
            return merged

        ctx = build_connections_context(sf)
        return list(ctx.store.list(_project_scope(request), protocol="mcp"))

    def _mcp_capability_item(conn: Any) -> dict[str, Any]:
        protocol_data = conn.protocol_data or {}
        tool_count = len(protocol_data.get("discovered_tools", []))
        return {
            "id": conn.id,
            "name": conn.display_name,
            "description": (
                f"{protocol_data.get('server_ref') or 'custom'} via "
                f"{protocol_data.get('transport', '?')} — "
                f"{tool_count} tools"
            ),
        }

    def _mcp_server_item(conn: Any) -> dict[str, Any]:
        protocol_data = conn.protocol_data or {}
        path = (
            protocol_data.get("server_ref")
            or protocol_data.get("url")
            or protocol_data.get("transport")
            or ""
        )
        return {
            **_mcp_capability_item(conn),
            "path": path,
            "status": conn.status,
            "transport": protocol_data.get("transport"),
            "tools_count": len(protocol_data.get("discovered_tools", [])),
        }

    @app.get("/playground/capabilities")
    async def playground_capabilities(request: Request) -> JSONResponse:
        """CapabilityCatalog with MCP servers sourced from registered connections.

        MCP servers are derived from the project's connections at request
        time (project-aware via ``X-Project-ID``), replacing the legacy
        hardcoded fixture list. Other capability buckets (tools, memory,
        guardrails, strategies) stay constant for now.
        """
        try:
            mcp_connections = await _mcp_connections_for_request(request)
        except _TenantStoreUnavailableError:
            raise
        except Exception:
            mcp_connections = []
        body = dict(_CAPABILITIES)
        body["mcp_servers"] = [_mcp_capability_item(c) for c in mcp_connections]
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
        await _enforce_run_quota(request)
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
        # CursorPage<PromptLogSummary> envelope: the UI reads page.items. A bare
        # array makes page.items undefined → the prompt-history table's .map()
        # crashes the page (observability/prompts) or silently empties it
        # (training/logs). Keep the existing filtering/limit behaviour, just wrap.
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
            logs = [r.to_dict() for r in records]
        else:
            data = sf._read()
            logs = [
                log
                for log in data.get("prompt_logs", [])
                if _in_read_scope(log.get("project_id"), request)
            ]
        return JSONResponse({"items": logs, "next_cursor": None, "has_more": False})

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
        await _enforce_run_quota(request)
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
                default_model = wf_def.get("default_model")

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
                    model = agent_def.get("model")
                    system_prompt = agent_def.get("system_prompt")
                    # Resolve a registered-agent reference (or any agent with no
                    # inline model) to its stored spec. Otherwise a `ref` agent —
                    # which carries no inline model in the YAML — ignores its
                    # configured model (e.g. a local ollama model) and falls back
                    # to the gpt-4o-mini default, which routes to OpenAI.
                    if not model or "ref" in agent_def:
                        spec = await _resolve_agent(request, agent_def.get("ref") or agent_name)
                        if spec:
                            model = model or spec.get("model")
                            system_prompt = system_prompt or spec.get("system_prompt")
                    model = model or default_model or "gpt-4o-mini"
                    system_prompt = system_prompt or f"You are the {agent_name} agent."
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
    async def workflow_dlq(request: Request) -> JSONResponse:
        """Failed workflow runs (the dead-letter queue), scoped to the requester.

        Failed runs are ``workflow_runs`` rows with ``status == "failed"`` — the
        same rows the retry/discard routes act on. Shaped for the admin DLQ table.
        """
        entries = [
            {
                "id": r.get("run_id"),
                "run_id": r.get("run_id"),
                "workflow_name": r.get("workflow_name", ""),
                "error": r.get("error", ""),
                "retry_count": r.get("retry_count", 0),
                "created_at": r.get("enqueued_at") or r.get("started_at") or "",
            }
            for r in _workflow_runs_for_request(request)
            if r.get("status") == "failed"
        ]
        return JSONResponse(entries)

    @app.post("/workflows/dlq/{run_id}/retry")
    async def workflow_dlq_retry(run_id: str, request: Request) -> JSONResponse:
        """Re-enqueue a failed workflow run under a fresh run_id, then drop the
        failed entry (lineage preserved via ``replay_of_run_id`` on the new run).

        Clones the original run's fields into the shared enqueue core, so a retry
        passes the same host-exec / Sealed-preview / worker-capability gates as a
        first run (e.g. a full-mode retry still needs an approved worker). The
        new run is charged to the *original* run's project, not the caller's hint.
        """
        target = _workflow_run_for_request(run_id, request, write=True)
        if target is None:
            return JSONResponse({"status": "not_found"}, status_code=404)
        if target.get("status") != "failed":
            return JSONResponse({"detail": "workflow run is not failed"}, status_code=409)
        target_pid = _workflow_record_project_id(target)

        # Re-enqueue first; if a gate rejects (403/400) the original is left intact.
        result = await _enqueue_workflow_run(
            request,
            pid=target_pid,
            workflow_name=target.get("workflow_name"),
            input_data=target.get("input_data", {}),
            execution_mode_str=target.get("execution_mode", "full"),
            security_profile_ref=target.get("security_profile_ref"),
            artifact_destination_raw=target.get("artifact_destination"),
            replay_of_run_id=run_id,
        )
        new_run_id = result["run_id"]

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

        sf.mutate(_apply)
        await _emit_audit(
            request,
            "workflow.dlq.retry",
            target_type="workflow_run",
            target_id=run_id,
            metadata={"new_run_id": new_run_id},
        )
        return JSONResponse({"new_run_id": new_run_id}, status_code=202)

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

    async def _enqueue_workflow_run(
        request: Request,
        *,
        pid: str | None,
        workflow_name: str,
        input_data: Any,
        execution_mode_str: str,
        security_profile_ref: str | None,
        artifact_destination_raw: Any,
        replay_of_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Shared core for enqueuing a workflow run.

        Validates execution_mode, applies the host-exec and Sealed
        identity-execution preview gates, capability-checks the fleet, resolves the
        Sealed cascade, persists a WorkflowRun row, and returns the 202 response
        body. Used by both POST /api/v1/workflows/enqueue and DLQ retry (which
        replays a failed run under a fresh run_id and threads ``replay_of_run_id``
        for lineage). The per-project run quota is enforced by the public enqueue
        route, not here — a DLQ retry is bounded by the existence of a failed run.
        """
        import secrets as _enq_sec

        from fastapi import HTTPException as _HTTPException

        from sagewai.artifacts.models import ArtifactDestination as _ArtifactDestination
        from sagewai.core.state import ExecutionMode as _ExecutionMode, WorkflowRun as _WorkflowRun, sandbox_mode_for as _sandbox_mode_for
        from sagewai.sandbox.models import SandboxMode as _SandboxMode

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

        # 2c. Sealed identity-execution preview gate: Modes 2/3/3b
        # (identity/full/full_jit) inject per-workload credentials and rely on
        # Sealed *runtime* enforcement that is experimental and not wired into
        # the default worker path. They are refused for tenants in multi-tenant
        # mode unless the operator opts in via SAGEWAI_SEALED_PREVIEW=1.
        # Single-org behaviour is unchanged (identity_execution_allowed → True).
        from sagewai.sandbox.policy import (
            identity_execution_allowed,
            identity_execution_preview_message,
            is_identity_execution_mode,
        )
        if is_identity_execution_mode(execution_mode) and not identity_execution_allowed():
            raise _HTTPException(
                status_code=403,
                detail=identity_execution_preview_message(execution_mode),
            )

        # 3. Capability check against fleet registry.
        # For non-BARE runs (requires_sandbox_mode=PER_RUN) we verify that at
        # least one APPROVED worker is registered. BARE runs execute inline and
        # need no worker.
        if requires_sandbox_mode != _SandboxMode.NONE:
            approved_workers = await fleet_registry.list_workers(
                org_id=_fleet_org_id(request),
                status=WorkerApprovalStatus.APPROVED,
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
            replay_of_run_id=replay_of_run_id,
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

        # 7. Resolved metadata for the 202 response body
        return {
            "run_id": run_id,
            "status": run.status.value,
            "execution_mode": run.execution_mode.value,
            "requires_sandbox_mode": run.requires_sandbox_mode.value,
            "security_profile_ref": run.security_profile_ref,
            "effective_env_keys": run.effective_env_keys,
            "effective_secret_keys": run.effective_secret_keys,
        }

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
        from fastapi import HTTPException as _HTTPException

        body = await request.json()
        pid = _project_scope(request)
        await _enforce_run_quota(request)

        workflow_name = body.get("workflow_name")
        if not workflow_name:
            raise _HTTPException(status_code=400, detail="workflow_name is required")

        result = await _enqueue_workflow_run(
            request,
            pid=pid,
            workflow_name=workflow_name,
            input_data=body.get("input_data", {}),
            execution_mode_str=body.get("execution_mode", "full"),
            security_profile_ref=body.get("security_profile_ref"),
            artifact_destination_raw=body.get("artifact_destination"),
        )
        return JSONResponse(result, status_code=202)

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
    async def workflow_registered_agents(request: Request) -> JSONResponse:
        agents = sf.list_agents(project_id=_project_scope(request))
        return JSONResponse([a.get("name", "") for a in agents])

    @app.get("/workflow-events/stream")
    async def workflow_events_stream() -> StreamingResponse:
        # Global live workflow-event feed for the dashboard's toast listener.
        # No event source is wired here yet, but the stream MUST stay open: a
        # generator that ends after one event makes the browser's EventSource
        # reconnect in a tight loop, flooding the backend and saturating the
        # per-origin connection limit (which then times out the dashboard's
        # health check → "Backend not reachable"). Hold it open with periodic
        # SSE heartbeat comments; the server cancels the generator on disconnect.
        return StreamingResponse(
            _workflow_events_sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Workflow registry (saved workflows) ────────────────────────

    @app.get("/api/v1/workflow-registry")
    async def list_saved_workflows(request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            workflows = await store.list_for(request.state.context, "saved_workflow")
            return JSONResponse({"items": workflows, "total": len(workflows)})
        data = sf._read()
        scope = _project_scope(request)
        workflows = _filter_items_for_scope(data.get("saved_workflows", []), scope)
        return JSONResponse({"items": workflows, "total": len(workflows)})

    @app.post("/api/v1/workflow-registry")
    async def save_workflow(request: Request) -> JSONResponse:
        _require_resource_write(request)
        body = await request.json()
        scope = _project_scope(request)
        import secrets as _sec
        wf = {
            "id": f"wf-{_sec.token_hex(6)}",
            "name": body.get("name", ""),
            "yaml_content": body.get("yaml_content", ""),
            "description": body.get("description", ""),
            "project_id": _owner(scope),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        store = _admin_resource_store(request)
        if store is not None:
            # Saved-workflow names are NOT unique (multiple workflows may share a
            # name; the by-name lookup resolves project-first), so no ``name=``.
            stored = await store.upsert_for(
                request.state.context, "saved_workflow", wf["id"], wf
            )
            await _emit_audit(request, "workflow_registry.create", target_type="workflow", target_id=wf["id"])
            return JSONResponse(stored, status_code=201)
        def _apply(d):
            d.setdefault("saved_workflows", []).append(wf)
        sf.mutate(_apply)
        await _emit_audit(request, "workflow_registry.create", target_type="workflow", target_id=wf["id"])
        return JSONResponse(wf, status_code=201)

    @app.get("/api/v1/workflow-registry/by-name/{name}")
    async def get_saved_workflow_by_name(name: str, request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            # Names aren't unique, so resolve via the in-scope list (project rows
            # before org-shared, matching the file path's _project_first).
            workflows = await store.list_for(request.state.context, "saved_workflow")
            for wf in _project_first(workflows):
                if wf.get("name") == name:
                    return JSONResponse(wf)
            return JSONResponse({"detail": "Not found"}, status_code=404)
        data = sf._read()
        scope = _project_scope(request)
        workflows = _filter_items_for_scope(data.get("saved_workflows", []), scope)
        for wf in _project_first(workflows):
            if wf.get("name") == name:
                return JSONResponse(wf)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.get("/api/v1/workflow-registry/{wf_id}")
    async def get_saved_workflow(wf_id: str, request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            wf = await store.get_for(request.state.context, "saved_workflow", wf_id)
            if wf is not None:
                return JSONResponse(wf)
            return JSONResponse({"detail": "Not found"}, status_code=404)
        data = sf._read()
        scope = _project_scope(request)
        for wf in _filter_items_for_scope(data.get("saved_workflows", []), scope):
            if wf.get("id") == wf_id:
                return JSONResponse(wf)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/workflow-registry/{wf_id}")
    async def delete_saved_workflow(wf_id: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        scope = _project_scope(request)
        store = _admin_resource_store(request)
        if store is not None:
            deleted = await store.delete_for(request.state.context, "saved_workflow", wf_id)
            if not deleted:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            await _emit_audit(request, "workflow_registry.delete", target_type="workflow", target_id=wf_id)
            return JSONResponse({"status": "ok"})
        def _apply(d):
            wfs = d.get("saved_workflows", [])
            remaining = []
            deleted = False
            for w in wfs:
                if w.get("id") == wf_id and _item_writable_in_scope(w, scope):
                    deleted = True
                    continue
                remaining.append(w)
            d["saved_workflows"] = remaining
            return deleted
        if sf.mutate(_apply):
            await _emit_audit(request, "workflow_registry.delete", target_type="workflow", target_id=wf_id)
            return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    # ── Budget limits ────────────────────────────────────────────

    @app.get("/api/v1/budget/limits")
    async def list_budget_limits(request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            return JSONResponse(
                await store.list_for(request.state.context, "budget_limit")
            )
        data = sf._read()
        return JSONResponse(_filter_items_for_scope(data.get("budget_limits", []), _project_scope(request)))

    @app.post("/api/v1/budget/limits")
    async def create_budget_limit(request: Request) -> JSONResponse:
        _require_resource_write(request)
        body = await request.json()
        scope = _project_scope(request)
        body.pop("project_id", None)
        body["project_id"] = _owner(scope)
        body.setdefault("created_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
        agent = body.get("agent_name", "")
        store = _admin_resource_store(request)
        if store is not None:
            stored = await store.upsert_for(
                request.state.context, "budget_limit", agent, body, name=agent
            )
            await _emit_audit(request, "budget_limit.upsert", target_type="budget_limit", target_id=agent)
            return JSONResponse(stored, status_code=201)
        def _apply(d):
            limits = d.setdefault("budget_limits", [])
            d["budget_limits"] = [
                l
                for l in limits
                if not (l.get("agent_name") == agent and _item_writable_in_scope(l, scope))
            ]
            d["budget_limits"].append(body)
        sf.mutate(_apply)
        await _emit_audit(request, "budget_limit.upsert", target_type="budget_limit", target_id=agent)
        return JSONResponse(body, status_code=201)

    @app.put("/api/v1/budget/limits/{agent_name}")
    async def update_budget_limit(agent_name: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        body = await request.json()
        body.pop("project_id", None)
        scope = _project_scope(request)
        store = _admin_resource_store(request)
        if store is not None:
            existing = await store.get_for(request.state.context, "budget_limit", agent_name)
            if existing is None:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            existing.update(body)
            stored = await store.upsert_for(
                request.state.context, "budget_limit", agent_name, existing, name=agent_name
            )
            await _emit_audit(request, "budget_limit.update", target_type="budget_limit", target_id=agent_name)
            return JSONResponse(stored)
        def _apply(d):
            for l in d.get("budget_limits", []):
                if l.get("agent_name") == agent_name and _item_writable_in_scope(l, scope):
                    l.update(body)
                    return l
            return None
        result = sf.mutate(_apply)
        if result is not None:
            await _emit_audit(request, "budget_limit.update", target_type="budget_limit", target_id=agent_name)
            return JSONResponse(result)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/budget/limits/{agent_name}")
    async def delete_budget_limit(agent_name: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        scope = _project_scope(request)
        store = _admin_resource_store(request)
        if store is not None:
            deleted = await store.delete_for(
                request.state.context, "budget_limit", agent_name
            )
            if not deleted:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            await _emit_audit(
                request, "budget_limit.delete", target_type="budget_limit", target_id=agent_name
            )
            return JSONResponse({"status": "ok"})
        state = {"deleted": False}
        def _apply(d):
            limits = d.get("budget_limits", [])
            remaining = []
            for l in limits:
                if l.get("agent_name") == agent_name and _item_writable_in_scope(l, scope):
                    state["deleted"] = True
                    continue
                remaining.append(l)
            d["budget_limits"] = remaining
        sf.mutate(_apply)
        if _multi_ctx(request) is not None and not state["deleted"]:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if state["deleted"]:
            await _emit_audit(
                request, "budget_limit.delete", target_type="budget_limit", target_id=agent_name
            )
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/budget/status/{agent_name}")
    async def get_budget_status(agent_name: str, request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            limit = await store.get_for(request.state.context, "budget_limit", agent_name)
        else:
            data = sf._read()
            limits = _project_first(
                _filter_items_for_scope(data.get("budget_limits", []), _project_scope(request))
            )
            limit = next((l for l in limits if l.get("agent_name") == agent_name), None)
        return JSONResponse({
            "agent_name": agent_name,
            "limit": limit,
            "current_spend_usd": 0.0,
            "remaining_usd": limit.get("daily_limit_usd", 0) if limit else 0,
            "status": "ok",
        })

    # ── Guardrail configs ────────────────────────────────────────

    @app.get("/api/v1/guardrails/configs")
    async def list_guardrail_configs(request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            return JSONResponse(
                await store.list_for(request.state.context, "guardrail_config")
            )
        data = sf._read()
        return JSONResponse(_filter_items_for_scope(data.get("guardrail_configs", []), _project_scope(request)))

    @app.get("/api/v1/guardrails/configs/{agent_name}")
    async def get_guardrail_config(agent_name: str, request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            config = await store.get_for(request.state.context, "guardrail_config", agent_name)
            if config:
                return JSONResponse(config)
            return JSONResponse({"detail": "Not found"}, status_code=404)
        data = sf._read()
        configs = _project_first(
            _filter_items_for_scope(data.get("guardrail_configs", []), _project_scope(request))
        )
        config = next((c for c in configs if c.get("agent_name") == agent_name), None)
        if config:
            return JSONResponse(config)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.put("/api/v1/guardrails/configs/{agent_name}")
    async def upsert_guardrail_config(agent_name: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        body = await request.json()
        scope = _project_scope(request)
        body.pop("project_id", None)
        body["agent_name"] = agent_name
        body["project_id"] = _owner(scope)
        store = _admin_resource_store(request)
        if store is not None:
            stored = await store.upsert_for(
                request.state.context, "guardrail_config", agent_name, body, name=agent_name
            )
            await _emit_audit(
                request, "guardrail_config.upsert", target_type="guardrail_config", target_id=agent_name
            )
            return JSONResponse(stored)
        def _apply(d):
            configs = d.setdefault("guardrail_configs", [])
            d["guardrail_configs"] = [
                c
                for c in configs
                if not (c.get("agent_name") == agent_name and _item_writable_in_scope(c, scope))
            ]
            d["guardrail_configs"].append(body)
        sf.mutate(_apply)
        await _emit_audit(
            request, "guardrail_config.upsert", target_type="guardrail_config", target_id=agent_name
        )
        return JSONResponse(body)

    @app.delete("/api/v1/guardrails/configs/{agent_name}/{guardrail_type}")
    async def delete_guardrail_config(agent_name: str, guardrail_type: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        scope = _project_scope(request)
        store = _admin_resource_store(request)
        if store is not None:
            # Sub-resource mutation: drop one guardrail type from the config's
            # list and re-persist. get_for hides cross-project rows (-> 404);
            # upsert_for re-stamps own scope and 403s an org-shared row.
            config = await store.get_for(request.state.context, "guardrail_config", agent_name)
            if config is None:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            config["guardrails"] = [
                g for g in config.get("guardrails", []) if g.get("type") != guardrail_type
            ]
            await store.upsert_for(
                request.state.context, "guardrail_config", agent_name, config, name=agent_name
            )
            await _emit_audit(
                request,
                "guardrail_config.delete",
                target_type="guardrail_config",
                target_id=agent_name,
                metadata={"guardrail_type": guardrail_type},
            )
            return JSONResponse({"status": "ok"})
        def _apply(d):
            for c in d.get("guardrail_configs", []):
                if c.get("agent_name") == agent_name and _item_writable_in_scope(c, scope):
                    types = c.get("guardrails", [])
                    c["guardrails"] = [g for g in types if g.get("type") != guardrail_type]
                    return True
            return False
        if sf.mutate(_apply):
            await _emit_audit(
                request,
                "guardrail_config.delete",
                target_type="guardrail_config",
                target_id=agent_name,
                metadata={"guardrail_type": guardrail_type},
            )
            return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    # ── Audit events ─────────────────────────────────────────────

    @app.get("/api/v1/audit/events")
    async def list_audit_events() -> JSONResponse:
        data = sf._read()
        # CursorPage<AuditEvent> envelope: the UI reads page.items / next_cursor /
        # has_more. A bare array makes page.items undefined → the audit table's
        # .map() crashes the page. Map the stored audit shape (id/ts/event_type/
        # actor_label/target/details) onto the UI's AuditEvent contract so rows
        # render with a populated agent, detail and time instead of blanks.
        items = []
        for e in reversed(data.get("audit_events", [])):
            details = e.get("details") or {}
            detail = e.get("target") or ", ".join(
                f"{k}={v}" for k, v in details.items()
            ) or None
            items.append({
                "id": e.get("id", ""),
                "agent_name": details.get("agent_name") or e.get("actor_label", ""),
                "event_type": e.get("event_type", ""),
                "detail": detail,
                "created_at": e.get("ts"),
            })
        return JSONResponse({"items": items, "next_cursor": None, "has_more": False})

    @app.get("/api/v1/audit/export")
    async def export_audit_events() -> JSONResponse:
        data = sf._read()
        return JSONResponse(data.get("audit_events", []))

    # ── API tokens ───────────────────────────────────────────────

    @app.get("/api/v1/tokens/")
    async def list_tokens(request: Request) -> JSONResponse:
        store = _token_store(request)
        if store is not None:
            # Project-bound + inherited org-shared tokens for this ctx (redacted).
            rows = await store.list_for(request.state.context)
            return JSONResponse(jsonable_encoder(rows))
        return JSONResponse(sf.list_api_tokens())

    @app.post("/api/v1/tokens/")
    async def create_token(request: Request) -> JSONResponse:
        require_org_admin(request.state.context)
        body = await request.json()
        store = _token_store(request)
        if store is not None:
            from sagewai.admin.api_token_store import TokenScopeError

            ctx = request.state.context
            scopes = set(body.get("scopes", ["read"]))
            try:
                record, plaintext = await store.create_for(
                    ctx,
                    name=body.get("name") or "Unnamed",
                    scopes=scopes,
                    project_id=ctx.project_id,  # from the session, never the body
                )
            except ValueError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)
            except TokenScopeError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=403)
            await _emit_audit(
                request,
                "token.created",
                target_type="api_token",
                target_id=record["id"],
                metadata={"scopes": record["scopes"], "project_id": record["project_id"]},
            )
            # Plaintext returned ONCE; the stored hash is never exposed.
            wire = {
                "id": record["id"],
                "name": record["name"],
                "scopes": record["scopes"],
                "project_id": record["project_id"],
                "token": plaintext,
            }
            return JSONResponse(jsonable_encoder(wire), status_code=201)
        entry = sf.create_api_token(name=body.get("name", "Unnamed"),
                                    scopes=body.get("scopes", ["read"]))
        emit_audit(sf, event_type="token.created",
                   actor_label=request.state.principal.actor_label,
                   target=entry["id"], details={"scopes": entry["scopes"]})
        return JSONResponse(entry, status_code=201)

    @app.post("/api/v1/tokens/{token_id}/revoke")
    async def revoke_token(token_id: str, request: Request) -> JSONResponse:
        require_org_admin(request.state.context)
        store = _token_store(request)
        if store is not None:
            if await store.revoke_for(request.state.context, token_id):
                await _emit_audit(
                    request, "token.revoked", target_type="api_token", target_id=token_id
                )
                return JSONResponse({"status": "ok"})
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if sf.revoke_api_token(token_id):
            emit_audit(sf, event_type="token.revoked",
                       actor_label=request.state.principal.actor_label, target=token_id)
            return JSONResponse({"status": "ok"})
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/tokens/{token_id}")
    async def delete_token(token_id: str, request: Request) -> JSONResponse:
        require_org_admin(request.state.context)
        store = _token_store(request)
        if store is not None:
            # Multi-tenant tokens are soft-deleted (revoked) — the row stays for
            # audit/hash-chain integrity. DELETE and revoke share one code path.
            if await store.revoke_for(request.state.context, token_id):
                await _emit_audit(
                    request, "token.deleted", target_type="api_token", target_id=token_id
                )
                return JSONResponse({"status": "ok"})
            return JSONResponse({"detail": "Not found"}, status_code=404)
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
    app.state.fleet_registry = fleet_registry
    app.state.fleet_task_store = fleet_task_store
    app.state.fleet_dispatcher = fleet_dispatcher

    def _fleet_org_id(request: Request) -> str:
        return _request_org_id(request)

    async def _fleet_worker_in_scope(worker_id: str, request: Request):
        worker = await fleet_registry.get_worker(worker_id)
        if worker is None or getattr(worker, "org_id", None) != _fleet_org_id(request):
            return None
        return worker

    async def _fleet_worker_authorized(
        worker_id: str, request: Request, *, require_approved: bool
    ):
        """Return (worker, None) when the caller may act for this worker, else
        (None, JSONResponse). Enforces org + project (write scope) + approval."""
        worker = await _fleet_worker_in_scope(worker_id, request)  # exists + same org
        if worker is None:
            return None, JSONResponse({"detail": "Not found"}, status_code=404)
        worker_project = worker.capabilities.labels.get("project_id")
        if not _in_write_scope(worker_project, request):  # same project (write scope)
            # 404, not 403: do not confirm a cross-project worker exists.
            return None, JSONResponse({"detail": "Not found"}, status_code=404)
        if require_approved and worker.approval_status != WorkerApprovalStatus.APPROVED:
            return None, JSONResponse(
                {"detail": "Worker not approved", "status": worker.approval_status.value},
                status_code=403,
            )
        return worker, None

    @app.post("/api/v1/fleet/register")
    async def fleet_register(request: Request) -> JSONResponse:
        """Worker self-registration."""
        body = await request.json()
        pid = _project_scope(request)
        labels = dict(body.get("labels") or {})
        labels.pop("project_id", None)  # never trust a body-supplied project scope
        caps = WorkerCapabilities(
            models_supported=body.get("models", []),
            pool=body.get("pool", "default"),
            labels=labels,
            max_concurrent=body.get("max_concurrent", 1),
        )
        # Stamp the project_id label from the token-derived scope only.
        project_label = _fleet_project_label(pid)
        if project_label:
            caps.labels["project_id"] = project_label
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
    async def fleet_claim(request: Request) -> Response:
        """Approved, in-scope worker claims a task matching its REGISTERED caps."""
        body = await request.json()
        worker, err = await _fleet_worker_authorized(
            body.get("worker_id", ""), request, require_approved=True
        )
        if err is not None:
            return err
        # Client-controlled long-poll window, clamped to a safe ceiling so a
        # worker cannot hold a server coroutine indefinitely.
        try:
            poll_timeout = min(max(float(body.get("poll_timeout", 5.0)), 0.0), 60.0)
        except (TypeError, ValueError):
            poll_timeout = 5.0
        caps = worker.capabilities
        task = await fleet_dispatcher.claim(
            worker_id=worker.id,
            org_id=_fleet_org_id(request),
            models_canonical=caps.models_canonical,
            pool=caps.pool,
            labels=caps.labels,
            poll_timeout=poll_timeout,
        )
        if task:
            return JSONResponse(task)
        # 204 must have no body.
        return Response(status_code=204)

    @app.post("/api/v1/fleet/report")
    async def fleet_report(request: Request) -> JSONResponse:
        """Approved, in-scope worker reports a task it owns."""
        body = await request.json()
        worker, err = await _fleet_worker_authorized(
            body.get("worker_id", ""), request, require_approved=True
        )
        if err is not None:
            return err
        try:
            await fleet_dispatcher.report(
                worker_id=worker.id,
                org_id=_fleet_org_id(request),
                run_id=body.get("run_id", ""),
                status=body.get("status", "completed"),
                output=body.get("output"),
                error=body.get("error"),
            )
        except NotTaskOwnerError:
            return JSONResponse({"detail": "Run not owned by this worker"}, status_code=403)
        return JSONResponse({"status": "ok"})

    @app.post("/api/v1/fleet/heartbeat")
    async def fleet_heartbeat(request: Request) -> JSONResponse:
        """Worker heartbeat — org + project scope, NO approval gate (pending
        workers must heartbeat to stay visible)."""
        body = await request.json()
        worker, err = await _fleet_worker_authorized(
            body.get("worker_id", ""), request, require_approved=False
        )
        if err is not None:
            return err
        await fleet_registry.heartbeat(worker.id, pool_stats=body.get("pool_stats"))
        return JSONResponse({"ok": True})

    @app.post("/api/v1/fleet/tasks")
    async def fleet_enqueue_task(request: Request) -> JSONResponse:
        """Operator producer: enqueue a task onto the (project-scoped) fleet queue."""
        import uuid as _uuid

        from sagewai.fleet.normalizer import ModelNormalizer

        body = await request.json()
        pid = _project_scope(request)
        labels = dict(body.get("labels") or {})
        labels.pop("project_id", None)  # never trust a body-supplied project scope
        project_label = _fleet_project_label(pid)
        if project_label:
            labels["project_id"] = project_label
        raw_model = body.get("model")
        model = ModelNormalizer.canonical_list([raw_model])[0] if raw_model else None
        run_id = str(_uuid.uuid4())
        task: dict = {
            "run_id": run_id,
            "org_id": _fleet_org_id(request),  # cross-org isolation at claim time
            "pool": body.get("pool", "default"),
            "labels": labels,
            "payload": body.get("payload", {}),
        }
        if model:
            task["model"] = model
        fleet_task_store.enqueue(task)
        logger.info(
            "Fleet task enqueued: run=%s pool=%s model=%s project=%s",
            run_id, task["pool"], model or "any", project_label or "global",
        )
        return JSONResponse(
            {"run_id": run_id, "pool": task["pool"], "model": model}, status_code=201
        )

    @app.get("/api/v1/fleet/workers")
    async def list_fleet_workers(request: Request) -> JSONResponse:
        workers = await fleet_registry.list_workers(org_id=_fleet_org_id(request))
        # The admin UI expects { workers: FleetWorker[], total } with the full
        # nested FleetWorker shape (capabilities, approval_status, …). Pydantic's
        # model_dump produces exactly that; this previously returned a flattened
        # bare array, so the page read `data.workers` as undefined and crashed.
        return JSONResponse({
            "workers": [
                {
                    # Enterprise fields the UI reads that the core model doesn't
                    # carry — default them so worker rows never crash on access.
                    "ip_allowlist": [],
                    "requires_dual_approval": False,
                    "connection_type": None,
                    **w.model_dump(mode="json"),
                }
                for w in workers
            ],
            "total": len(workers),
        })

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
        await _emit_audit(
            request, "fleet.worker.approved", target_type="fleet_worker", target_id=worker_id
        )
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
        await _emit_audit(
            request, "fleet.worker.rejected", target_type="fleet_worker", target_id=worker_id
        )
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
        await _emit_audit(
            request, "fleet.worker.revoked", target_type="fleet_worker", target_id=worker_id
        )
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
        # The admin UI expects { keys: FleetEnrollmentKey[], total } with the
        # full nested shape (allowed_pools/allowed_models arrays, …). Previously
        # this returned a flattened bare array, so the page read `data.keys` as
        # undefined and crashed. model_dump gives the right shape; key_hash is
        # excluded so the secret hash is never sent to the browser.
        return JSONResponse({
            "keys": [k.model_dump(mode="json", exclude={"key_hash"}) for k in keys],
            "total": len(keys),
        })

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
        await _emit_audit(
            request,
            "fleet.enrollment_key.created",
            target_type="fleet_enrollment_key",
            target_id=key_record.id,
        )
        return JSONResponse({
            "id": key_record.id, "key": raw_key, "name": key_record.name,
            "max_uses": key_record.max_uses,
        }, status_code=201)

    @app.delete("/api/v1/fleet/enrollment-keys/{key_id}")
    async def revoke_fleet_enrollment_key(key_id: str, request: Request) -> JSONResponse:
        require_org_admin(request.state.context)  # fleet management is org-level
        from fastapi import HTTPException
        keys = await fleet_registry.list_enrollment_keys(org_id=_fleet_org_id(request))
        if not any(k.id == key_id for k in keys):
            raise HTTPException(status_code=404, detail=f"Enrollment key {key_id} not found")
        try:
            await fleet_registry.revoke_enrollment_key(key_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        emit_audit(sf, event_type="fleet.enrollment_key.revoked",
                   actor_label=request.state.principal.actor_label, target=key_id)
        await _emit_audit(
            request,
            "fleet.enrollment_key.revoked",
            target_type="fleet_enrollment_key",
            target_id=key_id,
        )
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/fleet/audit")
    async def list_fleet_audit() -> JSONResponse:
        # The admin UI reads { events, total }; a bare array makes data.events
        # undefined and crashes the fleet audit view.
        return JSONResponse({"events": [], "total": 0})

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
        await _emit_audit(
            request, "connector.saved", target_type="connector", target_id=name
        )
        return JSONResponse(body)

    @app.post("/api/v1/connectors/{name}/test")
    async def test_connector(name: str) -> JSONResponse:
        return JSONResponse({"connected": True, "name": name})

    @app.delete("/api/v1/connectors/{name}")
    async def delete_connector(name: str, request: Request) -> JSONResponse:
        def _apply(d):
            connectors = d.get("connectors", [])
            d["connectors"] = [c for c in connectors if c.get("name") != name]
        sf.mutate(_apply)
        await _emit_audit(
            request, "connector.deleted", target_type="connector", target_id=name
        )
        return JSONResponse({"status": "ok"})

    # ── Notifications ────────────────────────────────────────────

    _NOTIFICATION_SECRET_KEYS = {
        "webhook_url",
        "email_api_key",
        "smtp_password",
        "api_key",
        "token",
        "secret",
        "password",
    }
    _REDACTED_MARKERS = {"***", "********", "***configured***"}

    def _notification_public(record: dict[str, Any]) -> dict[str, Any]:
        out = dict(record)
        for key in _NOTIFICATION_SECRET_KEYS:
            if out.get(key):
                out[key] = "***"
                out[f"has_{key}"] = True
        return out

    def _notification_type(record: dict[str, Any]) -> str:
        return str(record.get("channel_type") or record.get("type") or "")

    def _notification_channels_for_request(request: Request) -> list[dict[str, Any]]:
        data = sf._read()
        return _project_first(
            _filter_items_for_scope(data.get("notification_channels", []), _project_scope(request))
        )

    def _notification_channel_for_type(
        request: Request, channel_type: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                c
                for c in _notification_channels_for_request(request)
                if _notification_type(c) == channel_type
            ),
            None,
        )

    def _merge_notification_secrets(body: dict[str, Any], existing: dict[str, Any] | None) -> None:
        if existing is None:
            return
        for key in _NOTIFICATION_SECRET_KEYS:
            if body.get(key) in _REDACTED_MARKERS and existing.get(key):
                body[key] = existing[key]

    async def _encrypt_notification_secrets(record: dict[str, Any], ctx) -> None:
        """Encrypt every present plaintext secret field under the tenant key.

        Mutates ``record`` in place. Each secret is wrapped under the data key
        for ``ctx.project_id`` (org master key when org-shared); already-encrypted
        values pass through untouched (``encrypt_for_project`` is idempotent), so
        a redaction-then-restore re-save never double-wraps.
        """
        from sagewai.admin import tenant_keys

        for key in _NOTIFICATION_SECRET_KEYS:
            v = record.get(key)
            if isinstance(v, str) and v:
                record[key] = await tenant_keys.encrypt_for_project(
                    identity_store, ctx.org_id, ctx.project_id, v
                )

    async def _decrypt_notification_secret(
        value: str, row_project_id: str | None, ctx
    ) -> str:
        """Decrypt one stored notification secret for USE — FAIL CLOSED.

        Decrypts under the data key of the row's own ``project_id`` (org master
        key for org-shared rows). A value that cannot be decrypted (corrupt or
        missing key) raises :class:`NotificationSecretDecryptionError` so the
        send/test path can never fall back to the stored ciphertext or to a
        plaintext passthrough.
        """
        from sagewai.admin import tenant_keys
        from sagewai.sealed.crypto import SecretCorrupted

        try:
            return await tenant_keys.decrypt_for_project(
                identity_store, ctx.org_id, row_project_id, value
            )
        except SecretCorrupted as exc:
            raise NotificationSecretDecryptionError(
                "notification secret could not be decrypted"
            ) from exc

    async def _notification_channel_for_type_decrypted(
        request: Request, channel_type: str
    ) -> dict[str, Any] | None:
        """The in-scope channel of ``channel_type`` with secrets DECRYPTED for use.

        In multi mode the channel comes from the durable store (the same rows the
        CRUD routes write) and every secret is decrypted FAIL-CLOSED under the
        row's own project key — raises :class:`NotificationSecretDecryptionError`
        on an undecryptable secret so the caller surfaces an error and never sends
        the stored value. In single-org mode the file-backed channel is returned
        verbatim (no tenant-key model, no decryption) — the unchanged path.
        """
        ctx = _multi_ctx(request)
        if ctx is None:
            # Single-org: file-backed channel, secrets stored/used as-is.
            return _notification_channel_for_type(request, channel_type)
        store = _admin_resource_store(request)
        if store is not None:
            channels = _project_first(
                await store.list_for(request.state.context, "notification_channel")
            )
        else:
            channels = _notification_channels_for_request(request)
        channel = next(
            (c for c in channels if _notification_type(c) == channel_type), None
        )
        if channel is None:
            return None
        row_project_id = channel.get("project_id") or None
        out = dict(channel)
        for key in _NOTIFICATION_SECRET_KEYS:
            v = out.get(key)
            if isinstance(v, str) and v:
                out[key] = await _decrypt_notification_secret(v, row_project_id, ctx)
        return out

    @app.get("/api/v1/notifications/channels")
    async def list_notification_channels(request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            channels = await store.list_for(request.state.context, "notification_channel")
            return JSONResponse([_notification_public(c) for c in channels])
        return JSONResponse([
            _notification_public(c) for c in _notification_channels_for_request(request)
        ])

    @app.post("/api/v1/notifications/channels")
    async def save_notification_channel(request: Request) -> JSONResponse:
        _require_resource_write(request)
        import secrets as _sec
        body = await request.json()
        scope = _project_scope(request)
        body.pop("project_id", None)
        body["project_id"] = _owner(scope)
        if body.get("type") and not body.get("channel_type"):
            body["channel_type"] = body.get("type")
        body.setdefault("id", f"ch-{_sec.token_hex(6)}")
        store = _admin_resource_store(request)
        if store is not None:
            ctx = request.state.context
            # Preserve secrets the client redacted (sent back as the ``***``
            # marker) by copying the EXISTING stored (encrypted) value, then
            # encrypt any newly-supplied plaintext under the tenant key.
            existing = await store.get_for(ctx, "notification_channel", body["id"])
            _merge_notification_secrets(body, existing)
            await _encrypt_notification_secrets(body, ctx)
            try:
                await store.upsert_for(ctx, "notification_channel", body["id"], body)
            except ResourceWriteScopeError:
                return JSONResponse({"detail": "Forbidden"}, status_code=403)
            await _emit_audit(
                request,
                "notification.channel.upsert",
                target_type="notification_channel",
                target_id=body["id"],
            )
            return JSONResponse(_notification_public(body), status_code=201)
        def _apply(d):
            channels = d.setdefault("notification_channels", [])
            existing = next(
                (
                    c
                    for c in channels
                    if c.get("id") == body["id"] and _item_writable_in_scope(c, scope)
                ),
                None,
            )
            _merge_notification_secrets(body, existing)
            d["notification_channels"] = [
                c
                for c in channels
                if not (c.get("id") == body["id"] and _item_writable_in_scope(c, scope))
            ]
            d["notification_channels"].append(body)
        sf.mutate(_apply)
        await _emit_audit(
            request,
            "notification.channel.upsert",
            target_type="notification_channel",
            target_id=body["id"],
        )
        return JSONResponse(_notification_public(body), status_code=201)

    @app.delete("/api/v1/notifications/channels/{channel_id}")
    async def delete_notification_channel(channel_id: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        scope = _project_scope(request)
        store = _admin_resource_store(request)
        if store is not None:
            deleted = await store.delete_for(
                request.state.context, "notification_channel", channel_id
            )
            if not deleted:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            await _emit_audit(
                request,
                "notification.channel.delete",
                target_type="notification_channel",
                target_id=channel_id,
            )
            return JSONResponse({"status": "ok"})
        state = {"deleted": False}
        def _apply(d):
            channels = d.get("notification_channels", [])
            remaining = []
            for c in channels:
                if c.get("id") == channel_id and _item_writable_in_scope(c, scope):
                    state["deleted"] = True
                    continue
                remaining.append(c)
            d["notification_channels"] = remaining
        sf.mutate(_apply)
        if _multi_ctx(request) is not None and not state["deleted"]:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if state["deleted"]:
            await _emit_audit(
                request,
                "notification.channel.delete",
                target_type="notification_channel",
                target_id=channel_id,
            )
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/notifications/triggers")
    async def list_notification_triggers(request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            return JSONResponse(
                await store.list_for(request.state.context, "notification_trigger")
            )
        data = sf._read()
        return JSONResponse(
            _filter_items_for_scope(data.get("notification_triggers", []), _project_scope(request))
        )

    @app.post("/api/v1/notifications/triggers")
    async def save_notification_trigger(request: Request) -> JSONResponse:
        _require_resource_write(request)
        import secrets as _sec
        body = await request.json()
        scope = _project_scope(request)
        body.pop("project_id", None)
        body["project_id"] = _owner(scope)
        body.setdefault("id", f"tr-{_sec.token_hex(6)}")
        store = _admin_resource_store(request)
        if store is not None:
            try:
                await store.upsert_for(
                    request.state.context, "notification_trigger", body["id"], body
                )
            except ResourceWriteScopeError:
                return JSONResponse({"detail": "Forbidden"}, status_code=403)
            await _emit_audit(
                request,
                "notification.trigger.upsert",
                target_type="notification_trigger",
                target_id=body["id"],
            )
            return JSONResponse(body, status_code=201)
        def _apply(d):
            triggers = d.setdefault("notification_triggers", [])
            d["notification_triggers"] = [
                t
                for t in triggers
                if not (t.get("id") == body["id"] and _item_writable_in_scope(t, scope))
            ]
            d["notification_triggers"].append(body)
        sf.mutate(_apply)
        await _emit_audit(
            request,
            "notification.trigger.upsert",
            target_type="notification_trigger",
            target_id=body["id"],
        )
        return JSONResponse(body, status_code=201)

    @app.delete("/api/v1/notifications/triggers/{trigger_id}")
    async def delete_notification_trigger(trigger_id: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        scope = _project_scope(request)
        store = _admin_resource_store(request)
        if store is not None:
            deleted = await store.delete_for(
                request.state.context, "notification_trigger", trigger_id
            )
            if not deleted:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            await _emit_audit(
                request,
                "notification.trigger.delete",
                target_type="notification_trigger",
                target_id=trigger_id,
            )
            return JSONResponse({"status": "ok"})
        state = {"deleted": False}
        def _apply(d):
            triggers = d.get("notification_triggers", [])
            remaining = []
            for t in triggers:
                if t.get("id") == trigger_id and _item_writable_in_scope(t, scope):
                    state["deleted"] = True
                    continue
                remaining.append(t)
            d["notification_triggers"] = remaining
        sf.mutate(_apply)
        if _multi_ctx(request) is not None and not state["deleted"]:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if state["deleted"]:
            await _emit_audit(
                request,
                "notification.trigger.delete",
                target_type="notification_trigger",
                target_id=trigger_id,
            )
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/notifications/history")
    async def notification_history() -> JSONResponse:
        return JSONResponse([])

    @app.post("/api/v1/notifications/test")
    async def test_notification(request: Request) -> JSONResponse:
        """Send a test notification to the specified channel."""
        _require_resource_write(request)
        body = await request.json()
        channel_type = body.get("channel_type", "")

        # Find the saved channel config, with secrets decrypted under the row's
        # own project key — FAIL CLOSED: an undecryptable secret returns an error
        # rather than sending the stored ciphertext or any plaintext fallback.
        try:
            channel = await _notification_channel_for_type_decrypted(request, channel_type)
        except NotificationSecretDecryptionError:
            return JSONResponse(
                {"sent": False, "error": "Channel secret could not be decrypted."},
                status_code=500,
            )
        multi = _multi_ctx(request) is not None

        if channel_type == "slack":
            webhook_url = channel.get("webhook_url", "") if channel else ""
            if not webhook_url and not multi:
                webhook_url = body.get("webhook_url", "")
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
            provider = (channel or {}).get("email_provider", "")
            api_key = (channel or {}).get("email_api_key", "")
            from_email = (channel or {}).get("email_from", "")
            if not multi:
                provider = provider or os.environ.get("EMAIL_PROVIDER", "")
                api_key = api_key or os.environ.get("EMAIL_API_KEY", "")
                from_email = from_email or os.environ.get("EMAIL_FROM", "")
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
        _require_resource_write(request)
        body = await request.json()
        ch = body.get("channel", "")
        multi = _multi_ctx(request) is not None

        # Resolve the saved channel with secrets decrypted under the row's own
        # project key — FAIL CLOSED so the send path can never emit the stored
        # ciphertext or a plaintext fallback for an undecryptable secret.
        try:
            saved = await _notification_channel_for_type_decrypted(request, ch)
        except NotificationSecretDecryptionError:
            return JSONResponse(
                {"sent": False, "error": "Channel secret could not be decrypted."},
                status_code=500,
            )

        if ch == "slack":
            webhook_url = ""
            if saved is not None:
                webhook_url = saved.get("webhook_url", "")
            elif not multi:
                webhook_url = body.get("webhook_url", "")
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

            provider = (saved or {}).get("email_provider", "")
            api_key = (saved or {}).get("email_api_key", "")
            from_email = (saved or {}).get("email_from", "")
            if not multi:
                provider = provider or os.environ.get("EMAIL_PROVIDER", "")
                api_key = api_key or os.environ.get("EMAIL_API_KEY", "")
                from_email = from_email or os.environ.get("EMAIL_FROM", "")
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
    async def list_triggers(request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            return JSONResponse(await store.list_for(request.state.context, "connector_trigger"))
        data = sf._read()
        return JSONResponse(_filter_items_for_scope(data.get("triggers", []), _project_scope(request)))

    @app.post("/api/v1/triggers")
    async def create_trigger(request: Request) -> JSONResponse:
        _require_resource_write(request)
        import secrets as _sec
        body = await request.json()
        body.pop("project_id", None)
        body["project_id"] = _owner(_project_scope(request))
        body.setdefault("id", f"trig-{_sec.token_hex(6)}")
        store = _admin_resource_store(request)
        if store is not None:
            stored = await store.upsert_for(
                request.state.context, "connector_trigger", body["id"], body
            )
            await _emit_audit(request, "trigger.create", target_type="trigger", target_id=body["id"])
            return JSONResponse(stored, status_code=201)
        def _apply(d):
            d.setdefault("triggers", []).append(body)
        sf.mutate(_apply)
        await _emit_audit(request, "trigger.create", target_type="trigger", target_id=body["id"])
        return JSONResponse(body, status_code=201)

    @app.delete("/api/v1/triggers/{trigger_id}")
    async def delete_trigger(trigger_id: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        scope = _project_scope(request)
        store = _admin_resource_store(request)
        if store is not None:
            deleted = await store.delete_for(
                request.state.context, "connector_trigger", trigger_id
            )
            if not deleted:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            await _emit_audit(request, "trigger.delete", target_type="trigger", target_id=trigger_id)
            return JSONResponse({"status": "ok"})
        state = {"deleted": False}
        def _apply(d):
            triggers = d.get("triggers", [])
            remaining = []
            for t in triggers:
                if t.get("id") == trigger_id and _item_writable_in_scope(t, scope):
                    state["deleted"] = True
                    continue
                remaining.append(t)
            d["triggers"] = remaining
        sf.mutate(_apply)
        if _multi_ctx(request) is not None and not state["deleted"]:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if state["deleted"]:
            await _emit_audit(request, "trigger.delete", target_type="trigger", target_id=trigger_id)
        return JSONResponse({"status": "ok"})

    async def _set_trigger_enabled(
        trigger_id: str, request: Request, *, enabled: bool, action: str
    ) -> JSONResponse:
        # Enable/disable is a sub-resource mutation: read the trigger in scope,
        # flip ``enabled``, re-persist. get_for hides cross-project rows (-> 404);
        # upsert_for re-stamps own scope and 403s an org-shared row.
        store = _admin_resource_store(request)
        config = await store.get_for(request.state.context, "connector_trigger", trigger_id)
        if config is None:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        config["enabled"] = enabled
        stored = await store.upsert_for(
            request.state.context, "connector_trigger", trigger_id, config
        )
        await _emit_audit(request, action, target_type="trigger", target_id=trigger_id)
        return JSONResponse(stored)

    @app.patch("/api/v1/triggers/{trigger_id}/enable")
    async def enable_trigger(trigger_id: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        scope = _project_scope(request)
        store = _admin_resource_store(request)
        if store is not None:
            return await _set_trigger_enabled(
                trigger_id, request, enabled=True, action="trigger.enable"
            )
        def _apply(d):
            for t in d.get("triggers", []):
                if t.get("id") == trigger_id and _item_writable_in_scope(t, scope):
                    t["enabled"] = True
                    return t
            return None
        result = sf.mutate(_apply)
        if result is not None:
            await _emit_audit(request, "trigger.enable", target_type="trigger", target_id=trigger_id)
            return JSONResponse(result)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.patch("/api/v1/triggers/{trigger_id}/disable")
    async def disable_trigger(trigger_id: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        scope = _project_scope(request)
        store = _admin_resource_store(request)
        if store is not None:
            return await _set_trigger_enabled(
                trigger_id, request, enabled=False, action="trigger.disable"
            )
        def _apply(d):
            for t in d.get("triggers", []):
                if t.get("id") == trigger_id and _item_writable_in_scope(t, scope):
                    t["enabled"] = False
                    return t
            return None
        result = sf.mutate(_apply)
        if result is not None:
            await _emit_audit(request, "trigger.disable", target_type="trigger", target_id=trigger_id)
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
    async def list_mcp_servers(request: Request) -> JSONResponse:
        mcp_connections = await _mcp_connections_for_request(request)
        return JSONResponse([_mcp_server_item(c) for c in mcp_connections])

    async def _resolve_mcp_connection(request: Request, connection_id: str):
        """Return the scoped MCP connection by id, or None if not visible."""
        connections = await _mcp_connections_for_request(request)
        for conn in connections:
            if conn.id == connection_id:
                return conn
        return None

    def _mcp_plugin_context_for(request: Request, conn):
        """Build the single-org PluginContext + plugin for an MCP connection.

        Reuses the connections bootstrap so ``ctx.creds`` (the credentials
        router) is wired exactly as ``test()`` expects.
        """
        from sagewai.connections.bootstrap import build_connections_context
        from sagewai.connections.protocols.mcp import McpProtocolPlugin

        cctx = build_connections_context(sf)
        plugin = McpProtocolPlugin()
        plugin_ctx = cctx.make_plugin_context(
            project_id=conn.project_id, request=request
        )
        return plugin, plugin_ctx

    async def _open_mcp_client(plugin, plugin_ctx, conn):
        """Open an :class:`MCPClient` for ``conn`` following the test() recipe.

        Resolves effective config, decrypts + dispatches credentials, and
        returns an *un-entered* ``MCPClient`` the caller uses as an async
        context manager. stdio connections without host-exec are refused
        here (clean error, not a crash) since stdio launches a subprocess.
        """
        from sagewai.connections.protocols.mcp import MCPClient
        from sagewai.sandbox.policy import host_exec_allowed

        effective = plugin._resolve_effective_config(conn)
        if effective.get("transport") == "stdio" and not host_exec_allowed():
            raise _McpStdioRefusedError(
                "Host-backed execution disabled. Set SAGEWAI_ALLOW_HOST_EXEC=1 "
                "to enable stdio MCP servers (they launch local subprocesses)."
            )

        pd = conn.protocol_data
        if plugin_ctx.creds is not None:
            try:
                decrypted_pd = plugin_ctx.creds.decrypt(
                    pd,
                    sensitive_field_paths=plugin.sensitive_field_paths_for(conn),
                    connection_credentials_backend=conn.credentials_backend,
                )
            except Exception:
                decrypted_pd = pd
        else:
            decrypted_pd = pd
        decrypted_creds = decrypted_pd.get("credentials", {}) or {}
        env, headers = plugin._dispatch_credentials(conn, decrypted_creds)

        return MCPClient(
            transport=effective["transport"],
            command=effective.get("command"),
            args=effective.get("args"),
            url=effective.get("url"),
            env=env or None,
            headers=headers or None,
        )

    @app.post("/api/v1/mcp/discover")
    async def discover_mcp_tools(request: Request) -> JSONResponse:
        body = await request.json()
        connection_id = body.get("connection_id")
        if not connection_id:
            return JSONResponse({"error": "connection_id is required"}, status_code=400)
        conn = await _resolve_mcp_connection(request, connection_id)
        if conn is None:
            return JSONResponse(
                {"error": f"connection {connection_id} not found"}, status_code=404
            )
        # Prefer the cached capability list — avoids a live round-trip.
        cached = (conn.protocol_data or {}).get("discovered_tools")
        if cached:
            return JSONResponse({"tools": cached})

        plugin, plugin_ctx = _mcp_plugin_context_for(request, conn)
        try:
            client = await _open_mcp_client(plugin, plugin_ctx, conn)
        except _McpStdioRefusedError as exc:
            return JSONResponse({"error": str(exc)}, status_code=501)
        try:
            async with client as opened:
                tools = await opened.list_tools()
        except Exception as exc:  # noqa: BLE001 — surface as a clean error
            return JSONResponse(
                {"error": f"{type(exc).__name__}: {exc}"}, status_code=502
            )
        from sagewai.connections.protocols.mcp import _tool_attr

        return JSONResponse(
            {
                "tools": [
                    {
                        "name": _tool_attr(t, "name"),
                        "description": _tool_attr(t, "description", ""),
                        "input_schema": _tool_attr(t, "input_schema", {}) or {},
                    }
                    for t in tools
                ]
            }
        )

    @app.post("/api/v1/mcp/call")
    async def call_mcp_tool(request: Request) -> JSONResponse:
        body = await request.json()
        connection_id = body.get("connection_id")
        tool_name = body.get("tool") or body.get("tool_name")
        arguments = body.get("arguments") or {}
        if not connection_id:
            return JSONResponse(
                {"result": None, "error": "connection_id is required"},
                status_code=400,
            )
        if not tool_name:
            return JSONResponse(
                {"result": None, "error": "tool is required"}, status_code=400
            )
        conn = await _resolve_mcp_connection(request, connection_id)
        if conn is None:
            return JSONResponse(
                {"result": None, "error": f"connection {connection_id} not found"},
                status_code=404,
            )

        plugin, plugin_ctx = _mcp_plugin_context_for(request, conn)
        try:
            client = await _open_mcp_client(plugin, plugin_ctx, conn)
        except _McpStdioRefusedError as exc:
            return JSONResponse(
                {"result": None, "error": str(exc)}, status_code=501
            )
        try:
            async with client as opened:
                try:
                    result = await opened.call_tool(tool_name, arguments)
                except KeyError:
                    return JSONResponse(
                        {
                            "result": None,
                            "error": f"tool {tool_name!r} not found on connection",
                        },
                        status_code=404,
                    )
        except Exception as exc:  # noqa: BLE001 — surface as a clean error
            return JSONResponse(
                {"result": None, "error": f"{type(exc).__name__}: {exc}"},
                status_code=502,
            )
        return JSONResponse({"result": result, "error": None})

    # ── Context Engine ───────────────────────────────────────────
    #
    # Wired to the real ContextEngine / VectorMemory / GraphMemory engines via the
    # per-project resolver on ``app.state.memory_engines`` (attached by
    # setup_memory_engines — lifespan, or the test fixture under ASGITransport).
    #
    # PROJECT SCOPING (multi-tenant): each route derives its project from
    # ``_project_scope(request)`` — the session-validated RequestContext, NOT the
    # X-Project-ID header (a forged/foreign header is already 404'd or ignored by
    # the auth middleware). The resolver hands back an engine bound to that project,
    # so project A can never read or mutate project B's memory/context. Durable
    # backends (Postgres context store, file-durable sqlite-vec) are project-scoped.
    # In single-org mode the resolver maps every request to the "default" project
    # and uses the in-process engines (byte-identical to the prior behaviour).
    #
    # /api/v1/memory and /api/v1/context are member-writable (not in
    # _MULTI_ORG_PREFIXES); writes go through _require_resource_write (viewer=403).

    def _scope(request: Request) -> str | None:
        # The session-derived project filter (multi) / header filter (single-org).
        return _project_scope(request)

    def _context_engine(request: Request):
        resolver = getattr(app.state, "memory_engines", None)
        if resolver is None:
            return getattr(app.state, "context_engine", None)
        return resolver.context_for(_scope(request))

    def _vector_memory(request: Request):
        resolver = getattr(app.state, "memory_engines", None)
        if resolver is None:
            return getattr(app.state, "vector_memory", None)
        return resolver.vector_for(_scope(request))

    def _graph_memory(request: Request):
        resolver = getattr(app.state, "memory_engines", None)
        if resolver is None:
            return getattr(app.state, "graph_memory", None)
        return resolver.graph_for(_scope(request))

    @app.get("/api/v1/context/stats")
    async def context_stats(request: Request) -> JSONResponse:
        engine = _context_engine(request)
        if engine is None:
            return JSONResponse(
                {"status": "ok", "documents": 0, "chunks": 0,
                 "by_scope": {}, "by_source": {}, "by_status": {}}
            )
        docs = await engine.list_documents()
        by_scope: dict[str, int] = defaultdict(int)
        by_source: dict[str, int] = defaultdict(int)
        by_status: dict[str, int] = defaultdict(int)
        chunks = 0
        for d in docs:
            by_scope[d.scope.value] += 1
            by_source[d.source.value] += 1
            by_status[d.status] += 1
            chunks += d.chunk_count
        return JSONResponse({
            "status": "ok",
            "documents": len(docs),
            "chunks": chunks,
            "by_scope": dict(by_scope),
            "by_source": dict(by_source),
            "by_status": dict(by_status),
        })

    @app.get("/api/v1/context/scopes")
    async def context_scopes(request: Request) -> JSONResponse:
        from sagewai.context.models import ContextScope

        engine = _context_engine(request)
        rows = []
        for scope in ContextScope:
            doc_count = 0
            chunk_count = 0
            if engine is not None:
                docs = await engine.list_documents(scope=scope)
                doc_count = len(docs)
                chunk_count = sum(d.chunk_count for d in docs)
            rows.append({
                "scope": scope.value,
                "document_count": doc_count,
                "chunk_count": chunk_count,
            })
        return JSONResponse({"scopes": rows})

    @app.get("/api/v1/context/documents")
    async def list_context_documents(request: Request) -> JSONResponse:
        from sagewai.context.models import ContextScope, ContextSource

        engine = _context_engine(request)
        if engine is None:
            return JSONResponse({"documents": [], "count": 0, "total": 0})
        qp = request.query_params

        def _enum(cls, raw):
            try:
                return cls(raw) if raw else None
            except ValueError:
                return None

        limit = int(qp["limit"]) if qp.get("limit") else None
        offset = int(qp["offset"]) if qp.get("offset") else None
        tags = qp["tags"].split(",") if qp.get("tags") else None
        docs = await engine.list_documents(
            scope=_enum(ContextScope, qp.get("scope")),
            scope_id=qp.get("scope_id"),
            source=_enum(ContextSource, qp.get("source")),
            status=qp.get("status"),
            search=qp.get("search"),
            tags=tags,
            sort_by=qp.get("sort_by"),
            sort_order=qp.get("sort_order"),
            limit=limit,
            offset=offset,
        )
        total = await engine.count_documents(
            scope=_enum(ContextScope, qp.get("scope")),
            scope_id=qp.get("scope_id"),
            source=_enum(ContextSource, qp.get("source")),
            status=qp.get("status"),
            search=qp.get("search"),
            tags=tags,
        )
        items = [jsonable_encoder(d) for d in docs]
        return JSONResponse({"documents": items, "count": len(items), "total": total})

    @app.post("/api/v1/context/documents")
    async def upload_context_document(request: Request) -> JSONResponse:
        """Upload + ingest a file (the admin 'Add Knowledge → Upload Files')."""
        from sagewai.context.models import ContextScope

        engine = _context_engine(request)
        if engine is None:
            return JSONResponse(
                {"detail": "Context engine is not configured on this server"},
                status_code=503,
            )
        _require_resource_write(request)
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return JSONResponse({"detail": "No file provided"}, status_code=422)
        file_bytes = await upload.read()
        filename = getattr(upload, "filename", "") or "upload"
        try:
            scope = ContextScope(str(form.get("scope") or "org"))
        except ValueError:
            return JSONResponse(
                {"detail": f"Invalid scope: {form.get('scope')}"}, status_code=422
            )
        scope_id = str(form.get("scope_id") or "")
        enable_graph = str(form.get("enable_graph") or "").lower() in ("1", "true", "yes")
        tags_raw = str(form.get("tags") or "")
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        try:
            doc = await engine.ingest_file(
                file_bytes=file_bytes,
                filename=filename,
                scope=scope,
                scope_id=scope_id,
                enable_graph=enable_graph,
                metadata={"tags": tags} if tags else None,
            )
        except Exception as exc:  # noqa: BLE001 - surface ingestion failure to the UI
            logger.exception("Context document upload failed: %s", filename)
            return JSONResponse(
                {"detail": f"Ingestion failed: {exc}"}, status_code=500
            )
        await _emit_audit(
            request, "context.document.upload",
            target_type="context_document", target_id=doc.id,
        )
        return JSONResponse(
            {
                "status": "ok",
                "filename": filename,
                "message": f"Ingested {filename}",
                "document": jsonable_encoder(doc),
            },
            status_code=201,
        )

    @app.post("/api/v1/context/documents/text")
    async def ingest_context_text(request: Request) -> JSONResponse:
        """Ingest pasted text (the admin 'Add Knowledge → Paste Text')."""
        from sagewai.context.models import ContextScope, ContextSource

        engine = _context_engine(request)
        if engine is None:
            return JSONResponse(
                {"detail": "Context engine is not configured on this server"},
                status_code=503,
            )
        _require_resource_write(request)
        body = await request.json()
        text = str(body.get("text") or "").strip()
        title = str(body.get("title") or "Pasted text").strip()
        if not text:
            return JSONResponse({"detail": "No text provided"}, status_code=422)
        try:
            scope = ContextScope(str(body.get("scope") or "org"))
        except ValueError:
            return JSONResponse({"detail": "Invalid scope"}, status_code=422)
        try:
            source = ContextSource(str(body.get("source") or "manual"))
        except ValueError:
            source = ContextSource.MANUAL
        metadata = body.get("metadata") or None
        tags = body.get("tags") or []
        if tags:
            metadata = {**(metadata or {}), "tags": tags}
        try:
            doc = await engine.ingest_text(
                text=text,
                title=title,
                scope=scope,
                scope_id=str(body.get("scope_id") or ""),
                source=source,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 - surface ingestion failure to the UI
            logger.exception("Context text ingestion failed: %s", title)
            return JSONResponse(
                {"detail": f"Ingestion failed: {exc}"}, status_code=500
            )
        await _emit_audit(
            request, "context.document.ingest_text",
            target_type="context_document", target_id=doc.id,
        )
        return JSONResponse(
            {
                "status": "ok",
                "title": title,
                "message": f"Ingested {title}",
                "document": jsonable_encoder(doc),
            },
            status_code=201,
        )

    @app.post("/api/v1/context/search")
    async def context_search(request: Request) -> JSONResponse:
        from sagewai.context.models import ContextScope, ContextSource

        body = await request.json()
        query = body.get("query", "")
        top_k = int(body.get("top_k") or 5)
        engine = _context_engine(request)
        if engine is None or not query:
            return JSONResponse({"query": query, "results": [], "count": 0})

        def _enum_list(cls, raw):
            if not raw:
                return None
            out = []
            for v in raw:
                try:
                    out.append(cls(v))
                except ValueError:
                    continue
            return out or None

        results = await engine.search(
            query,
            top_k=top_k,
            scopes=_enum_list(ContextScope, body.get("scopes")),
            sources=_enum_list(ContextSource, body.get("sources")),
            tags=body.get("tags"),
        )
        items = [jsonable_encoder(r) for r in results]
        return JSONResponse({"query": query, "results": items, "count": len(items)})

    # ── Memory ───────────────────────────────────────────────────

    @app.get("/api/v1/memory/vector/stats")
    async def vector_stats(request: Request) -> JSONResponse:
        mem = _vector_memory(request)
        count = await _vector_doc_count(mem)
        return JSONResponse({
            "status": "ok",
            "documents": count,
            "backend": type(mem).__name__ if mem is not None else "none",
        })

    @app.post("/api/v1/memory/vector/search")
    async def vector_search(request: Request) -> JSONResponse:
        body = await request.json()
        query = body.get("query", "")
        top_k = int(body.get("top_k") or 5)
        mem = _vector_memory(request)
        if mem is None or not query:
            return JSONResponse({"query": query, "results": [], "count": 0})
        hits = await mem.retrieve(query, top_k=top_k)
        results = [{"content": c, "rank": i + 1} for i, c in enumerate(hits)]
        return JSONResponse({"query": query, "results": results, "count": len(results)})

    @app.post("/api/v1/memory/vector/ingest")
    async def vector_ingest(request: Request) -> JSONResponse:
        # Project members manage their own project's memory: the write perimeter
        # (_require_resource_write) denies a viewer (403); project isolation is
        # enforced by the per-project engine the resolver hands back. Single-org
        # is unchanged (scope is organizational, write gate is a no-op there).
        _require_resource_write(request)
        body = await request.json()
        content = body.get("content", "")
        metadata = body.get("metadata")
        mem = _vector_memory(request)
        if mem is None or not content:
            return JSONResponse({"status": "ok", "chunks": 0})
        await mem.store(content, metadata=metadata)
        return JSONResponse({"status": "ok", "chunks": 1})

    @app.get("/api/v1/memory/graph/stats")
    async def graph_stats(request: Request) -> JSONResponse:
        graph = _graph_memory(request)
        if graph is None:
            return JSONResponse(
                {"status": "ok", "entities": 0, "relations": 0, "backend": "none"}
            )
        return JSONResponse({
            "status": "ok",
            "entities": await graph.entity_count(),
            "relations": await graph.relation_count(),
            "backend": type(graph).__name__,
        })

    @app.post("/api/v1/memory/graph/query")
    async def graph_query(request: Request) -> JSONResponse:
        body = await request.json()
        query = body.get("query", "")
        top_k = int(body.get("top_k") or 5)
        graph = _graph_memory(request)
        if graph is None or not query:
            return JSONResponse({"query": query, "results": [], "count": 0})
        lines = await graph.retrieve(query, top_k=top_k)
        results = [{"content": c, "rank": i + 1} for i, c in enumerate(lines)]
        return JSONResponse({"query": query, "results": results, "count": len(results)})

    @app.post("/api/v1/memory/graph/entity")
    async def create_graph_entity(request: Request) -> JSONResponse:
        _require_resource_write(request)
        body = await request.json()
        name = body.get("name", "")
        metadata = body.get("metadata")
        graph = _graph_memory(request)
        if graph is not None and name:
            await graph.store(name, metadata=metadata)
        return JSONResponse({"status": "ok", "entity": name})

    @app.post("/api/v1/memory/graph/relation")
    async def create_graph_relation(request: Request) -> JSONResponse:
        _require_resource_write(request)
        body = await request.json()
        source = body.get("source", "")
        relation = body.get("relation", "")
        target = body.get("target", "")
        graph = _graph_memory(request)
        if graph is not None and source and relation and target:
            await graph.add_relation(source, relation, target)
        return JSONResponse({"status": "ok", "relation": relation})

    @app.get("/api/v1/memory/graph/entities")
    async def list_graph_entities(request: Request) -> JSONResponse:
        graph = _graph_memory(request)
        if graph is None:
            return JSONResponse({"entities": [], "count": 0})
        qp = request.query_params
        entities = await graph.list_entities(
            search=qp.get("search", ""),
            limit=int(qp["limit"]) if qp.get("limit") else 50,
            offset=int(qp["offset"]) if qp.get("offset") else 0,
        )
        return JSONResponse({"entities": entities, "count": len(entities)})

    @app.get("/api/v1/memory/graph/entity/{name}")
    async def get_graph_entity(name: str, request: Request) -> JSONResponse:
        graph = _graph_memory(request)
        meta = await graph.get_entity(name) if graph is not None else None
        if meta is None:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return JSONResponse({"name": name, "metadata": meta})

    @app.get("/api/v1/memory/graph/entity/{name}/neighbors")
    async def get_graph_neighbors(name: str, request: Request) -> JSONResponse:
        graph = _graph_memory(request)
        depth = int(request.query_params.get("depth", "1"))
        neighbors = (
            await graph.get_neighbors(name, depth=depth) if graph is not None else []
        )
        return JSONResponse({
            "entity": name,
            "depth": depth,
            "neighbors": neighbors,
            "count": len(neighbors),
        })

    @app.get("/api/v1/memory/graph/entity/{name}/relations")
    async def get_graph_relations(name: str, request: Request) -> JSONResponse:
        graph = _graph_memory(request)
        triples = await graph.get_relations(name) if graph is not None else []
        relations = [
            {"source": s, "relation": r, "target": t} for s, r, t in triples
        ]
        return JSONResponse({
            "entity": name,
            "relations": relations,
            "count": len(relations),
        })

    # ── Intelligence stack ───────────────────────────────────────
    #
    # Reports the runtime status of the pluggable intelligence components
    # (embedder, NER/relation extractors, language detection, multimodal
    # vision/transcription, graph). Each component resolves through the
    # ProviderRegistry's tiered fallback chains, so a missing optional dep
    # degrades to a zero-dep/stub backend rather than failing. This is a
    # plain authenticated read — the auth middleware gates it.

    @app.get("/api/v1/intelligence/status")
    async def intelligence_status() -> JSONResponse:
        # Introspection happens in a module helper so heavy optional intelligence
        # deps are imported lazily (never at serve.py import time) and every
        # component is wrapped — the endpoint never 500s.
        return JSONResponse(_intelligence_status_payload())

    # ── Eval ─────────────────────────────────────────────────────

    @app.get("/api/v1/eval/datasets")
    async def list_eval_datasets(request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            return JSONResponse(await store.list_for(request.state.context, "eval_dataset"))
        data = sf._read()
        return JSONResponse(_filter_items_for_scope(data.get("eval_datasets", []), _project_scope(request)))

    @app.post("/api/v1/eval/datasets")
    async def create_eval_dataset(request: Request) -> JSONResponse:
        _require_resource_write(request)
        import secrets as _sec
        body = await request.json()
        body.pop("project_id", None)
        body["project_id"] = _owner(_project_scope(request))
        body.setdefault("id", f"ds-{_sec.token_hex(6)}")
        body.setdefault("created_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
        store = _admin_resource_store(request)
        if store is not None:
            stored = await store.upsert_for(
                request.state.context, "eval_dataset", body["id"], body
            )
            await _emit_audit(request, "eval_dataset.create", target_type="eval_dataset", target_id=body["id"])
            return JSONResponse(stored, status_code=201)
        def _apply(d):
            d.setdefault("eval_datasets", []).append(body)
        sf.mutate(_apply)
        await _emit_audit(request, "eval_dataset.create", target_type="eval_dataset", target_id=body["id"])
        return JSONResponse(body, status_code=201)

    @app.get("/api/v1/eval/datasets/{dataset_id}")
    async def get_eval_dataset(dataset_id: str, request: Request) -> JSONResponse:
        store = _admin_resource_store(request)
        if store is not None:
            ds = await store.get_for(request.state.context, "eval_dataset", dataset_id)
            if ds is not None:
                return JSONResponse(ds)
            return JSONResponse({"detail": "Not found"}, status_code=404)
        data = sf._read()
        datasets = _filter_items_for_scope(data.get("eval_datasets", []), _project_scope(request))
        ds = next((d for d in datasets if d.get("id") == dataset_id), None)
        if ds:
            return JSONResponse(ds)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.delete("/api/v1/eval/datasets/{dataset_id}")
    async def delete_eval_dataset(dataset_id: str, request: Request) -> JSONResponse:
        _require_resource_write(request)
        scope = _project_scope(request)
        store = _admin_resource_store(request)
        if store is not None:
            deleted = await store.delete_for(request.state.context, "eval_dataset", dataset_id)
            if not deleted:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            await _emit_audit(
                request, "eval_dataset.delete", target_type="eval_dataset", target_id=dataset_id
            )
            return JSONResponse({"status": "ok"})
        state = {"deleted": False}
        def _apply(d):
            datasets = d.get("eval_datasets", [])
            remaining = []
            for ds in datasets:
                if ds.get("id") == dataset_id and _item_writable_in_scope(ds, scope):
                    state["deleted"] = True
                    continue
                remaining.append(ds)
            d["eval_datasets"] = remaining
        sf.mutate(_apply)
        if _multi_ctx(request) is not None and not state["deleted"]:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if state["deleted"]:
            await _emit_audit(
                request, "eval_dataset.delete", target_type="eval_dataset", target_id=dataset_id
            )
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
        # PUBLIC endpoint (no auth) — so the checks are cheap + in-process only:
        # a real state-file read plus a config inventory. No live DB probe (a
        # per-call connection would be a DoS vector) and no leaked error detail.
        # The DEEP readiness probe (live DB/store/key/audit/rate-limit checks)
        # is served by ``/api/v1/readyz`` (see ``_readyz_payload``).
        from sagewai.admin.tenancy import is_multi_tenant

        services: list[dict] = []
        overall = "healthy"
        try:
            sf._read()
            services.append({"name": "state_file", "status": "ok"})
        except Exception:
            services.append({"name": "state_file", "status": "error"})
            overall = "degraded"
        services.append({
            "name": "database",
            "status": "configured" if _resolve_database_url() else "not_configured",
        })
        services.append({
            "name": "tenancy",
            "status": "multi" if is_multi_tenant() else "single",
        })
        return JSONResponse({
            "status": overall,
            "sdk_version": version,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "services": services,
        })

    @app.get("/api/v1/readyz")
    async def readyz(request: Request) -> JSONResponse:
        # PUBLIC, DEEP readiness probe for orchestrators/ops. Unlike the shallow
        # /health/detailed (state-file read + config inventory), this actually
        # exercises the production dependencies (DB, tenant stores, master key,
        # audit store) and is 200 only when every *configured* dependency is
        # reachable, 503 otherwise. Because it is public it is leak-free: only
        # component STATUS strings ("ok"/"error"/"not_configured"), never error
        # messages, connection strings, or secrets. It reuses the app's already-
        # pooled engines/stores (no fresh per-call pool — that would be a DoS
        # vector). "not_configured" is neutral (single-org has no DB/tenant
        # stores) and does NOT make the service "not ready".
        payload = await _readyz_payload(request, sf)
        return JSONResponse(payload, status_code=200 if payload["ready"] else 503)

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


def _readyz_db_engine(request: Request):
    """An already-built ``AsyncEngine`` to reuse for the ``SELECT 1`` DB probe, or
    None if no store engine is wired.

    Crucially this REUSES a pooled engine the app already holds — the identity
    store, the resource-store bundle, or the tenant-audit store — rather than
    creating a fresh engine/pool per call (which would be a DoS vector on a
    public endpoint). It is its own helper so a test can stub a single seam.
    """
    state = getattr(request.app, "state", None)
    candidates = []
    ident = getattr(state, "identity_store", None)
    candidates.append(getattr(ident, "_engine", None))
    rs = getattr(state, "resource_stores", None)
    if rs is not None:
        for name in ("admin_resource", "provider", "agent", "connection", "run", "prompt_log"):
            store = getattr(rs, name, None)
            candidates.append(getattr(store, "_engine", None))
    audit = getattr(state, "tenant_audit", None)
    candidates.append(getattr(audit, "engine", None) or getattr(audit, "_engine", None))
    for engine in candidates:
        if engine is not None:
            return engine
    return None


async def _readyz_payload(request: Request, sf: AdminStateFile) -> dict[str, Any]:
    """Build the deep-readiness payload by probing each production dependency.

    Every probe is wrapped in its own try/except → ``"error"`` on any exception
    (the handler never raises). A dependency that isn't configured reports
    ``"not_configured"`` (neutral — doesn't fail readiness). ``ready`` is true iff
    no *configured* dependency is in ``"error"``. Only status strings are
    returned — never error detail, connection strings, or secrets.
    """
    from sqlalchemy import text

    from sagewai.admin.tenancy import is_multi_tenant

    multi = is_multi_tenant()
    checks: list[dict[str, str]] = []

    def _add(name: str, status: str) -> None:
        checks.append({"name": name, "status": status})

    # state_file — the single-org core store must read.
    try:
        sf._read()
        _add("state_file", "ok")
    except Exception:
        _add("state_file", "error")

    # database — reuse an EXISTING pooled engine for SELECT 1. not_configured if
    # no DB is configured and no store engine is available (single-org default).
    db_status = "not_configured"
    try:
        engine = _readyz_db_engine(request)
        if _resolve_database_url() is not None or engine is not None:
            if engine is None:
                # configured via env but no engine wired — treat as reachable-
                # unknown; report not_configured rather than spinning a pool.
                db_status = "not_configured"
            else:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                db_status = "ok"
    except Exception:
        db_status = "error"
    _add("database", db_status)

    # identity_store — multi mode only: present + reachable via its engine.
    ident_status = "not_configured"
    if multi:
        try:
            ident = getattr(request.app.state, "identity_store", None)
            engine = getattr(ident, "_engine", None)
            if ident is None or engine is None:
                ident_status = "error"
            else:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                ident_status = "ok"
        except Exception:
            ident_status = "error"
    _add("identity_store", ident_status)

    # resource_store — multi mode only: the generic AdminResourceStore is wired
    # on the bundle (durable control-plane backing). not_configured in single-org.
    res_status = "not_configured"
    if multi:
        try:
            rs = getattr(request.app.state, "resource_stores", None)
            store = getattr(rs, "admin_resource", None) if rs is not None else None
            engine = getattr(store, "_engine", None)
            if store is None or engine is None:
                res_status = "error"
            else:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                res_status = "ok"
        except Exception:
            res_status = "error"
    _add("resource_store", res_status)

    # tenant_audit — multi mode only: app.state.tenant_audit present + reachable.
    audit_status = "not_configured"
    if multi:
        try:
            audit = getattr(request.app.state, "tenant_audit", None)
            engine = getattr(audit, "engine", None) or getattr(audit, "_engine", None)
            if audit is None or engine is None:
                audit_status = "error"
            else:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                audit_status = "ok"
        except Exception:
            audit_status = "error"
    _add("tenant_audit", audit_status)

    # master_key — multi mode only: a lightweight custody check that resolves a
    # key SOURCE without decrypting anything sensitive. If encrypted provider
    # secrets exist the org master key MUST resolve (error otherwise — the
    # service can't serve those secrets); else it is neutral (ok if a key source
    # is available, not_configured if none is). Mirrors the startup fail-closed
    # check (_require_tenant_provider_key_if_encrypted) without touching ciphertext.
    key_status = "not_configured"
    if multi:
        try:
            from sagewai.admin import tenant_keys

            rs = getattr(request.app.state, "resource_stores", None)
            provider = getattr(rs, "provider", None) if rs is not None else None
            has_encrypted = False
            if provider is not None:
                has_encrypted = await provider.has_encrypted_secrets()
            try:
                tenant_keys.org_crypto()  # resolves the master key; raises if none
                key_status = "ok"
            except Exception:
                # a missing key is only an ERROR when encrypted secrets exist
                # (otherwise no key has been provisioned yet — neutral).
                key_status = "error" if has_encrypted else "not_configured"
        except Exception:
            key_status = "error"
    _add("master_key", key_status)

    # rate_limit — multi mode only: the distributed limiter backing login lockout
    # and per-project run quotas. A Postgres-backed limiter shares the tenant
    # engine; confirm the throttle is wired and (if Postgres-backed) reachable.
    # An in-memory limiter in multi is a valid single-replica config (ok).
    rl_status = "not_configured"
    if multi:
        try:
            throttle = getattr(request.app.state, "run_throttle", None)
            limiter = getattr(throttle, "_limiter", None)
            if throttle is None or limiter is None:
                rl_status = "error"
            else:
                eng = getattr(limiter, "_engine", None)
                if eng is not None:
                    async with eng.connect() as conn:
                        await conn.execute(text("SELECT 1"))
                rl_status = "ok"
        except Exception:
            rl_status = "error"
    _add("rate_limit", rl_status)

    ready = not any(c["status"] == "error" for c in checks)
    return {"ready": ready, "checks": checks}


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


def _request_org_id(request: Request) -> str:
    """Return the server-resolved umbrella org for this request.

    ``org_id`` is an internal namespace, not a tenant selector. Auth middleware
    derives it from the session/context; legacy single-org paths fall back to the
    process default. Routes must not accept it from request bodies or query params.
    """
    ctx = getattr(request.state, "context", None)
    return ctx.org_id if ctx is not None else "default"


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


def _fleet_project_label(scope: str | None) -> str | None:
    """Project label for fleet routing, or None for org-shared/no project scope."""
    return _owner(scope)


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


def _admin_resource_store(request: Request):
    """The active generic admin-resource store, or None in single-org mode.

    The durable backing for every file-backed control-plane resource (budgets,
    guardrails, ...). When present, the resource routes use it (ctx-scoped,
    keyed by ``kind``) instead of the file store; when None they keep their
    unchanged ``sf.*`` path."""
    return _tenant_store_or_none(request, "admin_resource")


def _token_store(request: Request):
    """The active tenant API-token store, or None in single-org mode.

    When present, the /api/v1/tokens routes mint/list/revoke project-bound tokens
    via this ctx-scoped store; when None they keep their unchanged ``sf.*`` path
    (the single-org file-backed token list)."""
    return _tenant_store_or_none(request, "api_token")


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


def _item_readable_in_scope(item: dict, scope: str | None) -> bool:
    """Read filter for file-backed project-aware resources."""
    if scope is None:
        return True
    item_project_id = item.get("project_id")
    if scope == SHARED_ONLY:
        return item_project_id in (None, "")
    return item_project_id in (scope, None, "")


def _item_writable_in_scope(item: dict, scope: str | None) -> bool:
    """Write filter for file-backed project-aware resources."""
    if scope is None:
        return True
    item_project_id = item.get("project_id")
    if scope == SHARED_ONLY:
        return item_project_id in (None, "")
    return item_project_id == scope


def _filter_items_for_scope(items: list[dict], scope: str | None) -> list[dict]:
    return [item for item in items if _item_readable_in_scope(item, scope)]


def _project_first(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: item.get("project_id") in (None, ""))


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


# Durable, hash-chained, per-tenant W8 audit (`_emit_audit`) is **multi-tenant
# only** — a no-op in single-org mode, where the foundation file audit
# (`emit_audit(sf, ...)`) records auth events plus org/token/fleet admin actions.
#
# In multi mode, durable audit currently covers: org/project settings update;
# provider upsert/delete/set-default; agent create/delete; prompt-log create/
# update/delete; budget create/update/delete; guardrail upsert/delete;
# notification channel/trigger upsert/delete; autopilot trigger create; workflow-
# registry save; workflow run approve/reject; artifact-destination upsert/delete;
# API-token create/revoke/delete; fleet worker approve/reject/revoke; fleet
# enrollment-key create/revoke; connector save/delete. Remaining durable-audit
# gap (tracked in a parallel PR, NOT a single-org-launch blocker): memory/context
# ingest.
class NotificationSecretDecryptionError(RuntimeError):
    """A stored notification secret could not be decrypted; the send/test path
    fails closed (never emits the stored ciphertext or a plaintext fallback)."""


class _AuditUnavailableError(Exception):
    """Durable audit could not be recorded — the write fails closed (HTTP 503)."""


class _TenantStoreUnavailableError(Exception):
    """A multi-tenant resource route has no tenant store wired."""

    def __init__(self, store_name: str) -> None:
        super().__init__(store_name)
        self.store_name = store_name


class _McpStdioRefusedError(Exception):
    """A stdio MCP server was requested but host-exec is disabled."""


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
    """Per-project run-start rate limiter: caps run-starts per window so one
    project cannot starve others. ``limit <= 0`` disables it (always allow).

    Delegates counting to a :class:`~sagewai.db.rate_limit.RateLimiter`. The
    default is an in-memory sliding window (single-process, single-org default,
    byte-identical to the legacy throttle); in multi-tenant mode it is built with
    a :class:`PostgresRateLimiter` so the limit is enforced across worker
    processes (see :func:`build_rate_limiter`).
    """

    def __init__(self, limit: int, window: float, limiter=None) -> None:
        from sagewai.db.rate_limit import InMemoryRateLimiter, RateLimiter

        self.limit = limit
        self.window = window
        self._limiter: RateLimiter = (
            limiter if limiter is not None else InMemoryRateLimiter()
        )

    def allow(self, key: Any) -> bool:
        """Synchronous allow — in-memory limiter only (single-org / tests)."""
        from sagewai.db.rate_limit import InMemoryRateLimiter

        if not isinstance(self._limiter, InMemoryRateLimiter):
            raise RuntimeError("distributed limiter requires allow_async()")
        return self._limiter.hit_sync(str(key), limit=self.limit, window=self.window)

    async def allow_async(self, key: Any) -> bool:
        """Async allow — used in request handlers; supports the Postgres backend."""
        if self.limit <= 0:
            return True
        return await self._limiter.hit(str(key), limit=self.limit, window=self.window)


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


async def _enforce_run_quota(request: Request) -> None:
    """Per-project run-start rate quota (multi-tenant only; W7).

    Non-admin run-start requires a concrete project context: an org:member who
    omits X-Project-ID would otherwise land in the shared ``(org, None)`` bucket
    and bypass their per-project rate, so we require a selected project and charge
    org-shared execution to *that* project's bucket. Org owners/admins may run in
    org scope (their own bucket).

    **Scope:** the underlying limiter is chosen by :func:`build_rate_limiter` —
    distributed (Postgres-backed, correct across worker processes) in multi-tenant
    mode, in-memory single-process in single-org. This closes the W7 fairness gap
    for multi-replica deployments.

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
    if not await throttle.allow_async((ctx.org_id, ctx.project_id)):
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
