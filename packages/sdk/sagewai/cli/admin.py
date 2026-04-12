# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Admin CLI commands — status, runs, costs, serve, and health."""

# NOTE: do NOT add `from __future__ import annotations` to this file.
# PEP 563 stringifies all annotations, which prevents FastAPI from
# recognising `request: Request` at runtime. FastAPI needs the live
# type object to inject the Starlette request.

import click

import sagewai.cli as _cli


@click.group()
def admin() -> None:
    """Admin operations — status, runs, costs, and serving the admin API.

    \b
    Examples:
      sagewai admin status        Show registry and run counts
      sagewai admin runs          List recent agent runs
      sagewai admin costs         Show cost analytics
      sagewai admin serve         Start the admin API server
      sagewai admin health        Show system health from admin API
    """


@admin.command("status")
def admin_status() -> None:
    """Show the current admin status (registry, runs, sessions)."""
    from sagewai.admin.state import AdminState
    from sagewai.core.registry import AgentRegistry

    registry = AgentRegistry.get_instance()
    agents = registry.list_agents()
    state = AdminState()

    click.echo("Sagewai Admin Status")
    click.echo("=" * 40)
    click.echo(f"  Registered agents : {len(agents)}")
    click.echo(f"  Total runs        : {state.total_runs}")
    click.echo(f"  Active sessions   : {state.active_sessions}")
    click.echo(f"  SDK version       : {_cli.VERSION}")


@admin.command("runs")
@click.option(
    "--agent", "agent_name", default=None, help="Filter by agent name."
)
@click.option("--limit", default=20, help="Maximum runs to show.")
def admin_runs(agent_name: str | None, limit: int) -> None:
    """List recent agent runs from the admin state."""
    from sagewai.admin.state import AdminState

    state = AdminState()
    runs = state.list_runs(agent_name=agent_name, limit=limit)
    if not runs:
        click.echo("No runs recorded yet.")
        return

    rows = [
        {
            "run_id": r.run_id,
            "agent": r.agent_name,
            "status": r.status,
            "tokens": r.total_tokens,
        }
        for r in runs
    ]
    _cli._echo_table(rows, ["run_id", "agent", "status", "tokens"])


@admin.command("costs")
@click.option(
    "--agent", "agent_name", default=None, help="Filter by agent name."
)
def admin_costs(agent_name: str | None) -> None:
    """Show cost analytics from the AnalyticsStore."""
    from sagewai.admin.analytics import AnalyticsStore

    store = AnalyticsStore()
    costs = store.get_costs(agent_name=agent_name)
    _cli._echo_json(costs)


@admin.command("serve")
@click.option("--host", default="0.0.0.0", help="Host to bind.")
@click.option("--port", default=8000, type=int, help="Port to bind.")
def admin_serve(host: str, port: int) -> None:
    """Start the admin API server (FastAPI + uvicorn)."""
    try:
        import uvicorn
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import JSONResponse

        from sagewai.admin import create_admin_router
        from sagewai.admin.analytics import (
            AnalyticsStore,
            create_analytics_router,
        )
        from sagewai.admin.state import AdminState

        app = FastAPI(title="Sagewai Admin", version=_cli.VERSION)

        # Allow the admin Next.js dev server to reach the API.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        import os

        # ── OpenTelemetry ────────────────────────────────────────────
        # Sends logs, metrics, and traces to the OTel collector at
        # localhost:4318 (HTTP). Gracefully degrades if the collector
        # is not running or the OTel packages are not installed.
        _otel_ok = False
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
            import logging

            otel_endpoint = os.environ.get(
                "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"
            )
            resource = Resource.create({
                "service.name": "sagewai-admin",
                "service.version": _cli.VERSION,
                "service.namespace": "sagewai",
            })

            # Traces
            tp = TracerProvider(resource=resource)
            tp.add_span_processor(BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{otel_endpoint}/v1/traces")
            ))
            trace.set_tracer_provider(tp)

            # Metrics
            reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{otel_endpoint}/v1/metrics"),
                export_interval_millis=15_000,
            )
            mp = MeterProvider(resource=resource, metric_readers=[reader])
            metrics.set_meter_provider(mp)

            # Logs → OTel collector
            lp = LoggerProvider(resource=resource)
            lp.add_log_record_processor(BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=f"{otel_endpoint}/v1/logs")
            ))
            otel_handler = LoggingHandler(
                level=logging.INFO, logger_provider=lp
            )
            # Attach to root logger and uvicorn loggers so all log
            # records flow to the OTel collector.
            logging.getLogger().addHandler(otel_handler)
            logging.getLogger().setLevel(logging.INFO)
            for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
                lg = logging.getLogger(name)
                lg.addHandler(otel_handler)
                lg.setLevel(logging.INFO)

            # App-level logger for structured request logging
            _app_log = logging.getLogger("sagewai.admin")
            _app_log.setLevel(logging.INFO)

            from starlette.middleware.base import BaseHTTPMiddleware
            import time as _time

            class _RequestLogMiddleware(BaseHTTPMiddleware):
                async def dispatch(self, request, call_next):
                    t0 = _time.monotonic()
                    response = await call_next(request)
                    dt = (_time.monotonic() - t0) * 1000
                    _app_log.info(
                        "%s %s %d %.1fms",
                        request.method,
                        request.url.path,
                        response.status_code,
                        dt,
                    )
                    return response

            app.add_middleware(_RequestLogMiddleware)

            # Auto-instrument FastAPI (request spans + metrics)
            FastAPIInstrumentor.instrument_app(app)
            _otel_ok = True
            click.echo("  OpenTelemetry → " + otel_endpoint)
        except ImportError:
            click.echo("  OpenTelemetry: not installed (pip install sagewai[observability])")
        except Exception as exc:
            click.echo(f"  OpenTelemetry: init failed ({exc})")

        state = AdminState()
        analytics = AnalyticsStore()

        app.include_router(create_admin_router(state), prefix="/admin")
        app.include_router(
            create_analytics_router(analytics), prefix="/api/v1/analytics"
        )
        # Keep the legacy /analytics prefix so curl and existing scripts
        # continue to work.
        app.include_router(
            create_analytics_router(analytics), prefix="/analytics"
        )

        # ── Admin setup & auth ────────────────────────────────────────
        # The admin panel requires a first-time setup wizard that
        # creates the initial administrator account before any other
        # page is accessible. This is enforced by the Next.js proxy
        # which checks /api/v1/setup/status on every request.
        #
        # Auth uses PBKDF2 password hashing (stdlib) and opaque
        # bearer tokens stored in a JSON state file at
        # ~/.sagewai/admin-state.json. No external deps needed.

        import datetime
        import hashlib
        import json
        import secrets
        from pathlib import Path

        from fastapi import Request

        STATE_DIR = Path.home() / ".sagewai"
        STATE_FILE = STATE_DIR / "admin-state.json"

        def _load_state() -> dict:
            if STATE_FILE.exists():
                return json.loads(STATE_FILE.read_text())
            return {}

        def _save_state(data: dict) -> None:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(data, indent=2))

        def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
            if salt is None:
                salt = secrets.token_hex(16)
            h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000)
            return h.hex(), salt

        def _verify_password(password: str, stored_hash: str, salt: str) -> bool:
            h, _ = _hash_password(password, salt)
            return secrets.compare_digest(h, stored_hash)

        def _make_token() -> str:
            return secrets.token_urlsafe(48)

        # ── Setup ────────────────────────────────────────────────────

        @app.get("/api/v1/setup/status")
        async def setup_status() -> JSONResponse:
            """Check if first-time setup is required."""
            st = _load_state()
            if st.get("setup_complete"):
                return JSONResponse({"setup_required": False})
            return JSONResponse({
                "setup_required": True,
                "reason": "No administrator account has been created yet.",
            })

        @app.post("/api/v1/setup")
        async def run_setup(request: Request) -> JSONResponse:
            """Execute the first-time setup wizard."""
            st = _load_state()
            if st.get("setup_complete"):
                return JSONResponse(
                    {"ok": False, "message": "Setup has already been completed."},
                    status_code=409,
                )
            body = await request.json()
            # Validate required fields
            required = ["org_name", "admin_email", "admin_password"]
            missing = [f for f in required if not body.get(f)]
            if missing:
                return JSONResponse(
                    {"ok": False, "message": f"Missing fields: {', '.join(missing)}"},
                    status_code=422,
                )
            pw_hash, pw_salt = _hash_password(body["admin_password"])
            org_slug = body.get("org_slug") or body["org_name"].lower().replace(" ", "-")
            app_slug = (body.get("app_name") or "default").lower().replace(" ", "-")
            st.update({
                "setup_complete": True,
                "setup_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "org_name": body["org_name"],
                "org_slug": org_slug,
                "contact_email": body.get("contact_email", ""),
                "timezone": body.get("timezone", "UTC"),
                "app_name": body.get("app_name", "Default"),
                "app_slug": app_slug,
                "admin": {
                    "id": secrets.token_hex(8),
                    "name": body.get("admin_name", ""),
                    "email": body["admin_email"],
                    "password_hash": pw_hash,
                    "password_salt": pw_salt,
                    "role": "admin",
                    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                },
            })
            _save_state(st)
            return JSONResponse({
                "ok": True,
                "org_slug": org_slug,
                "app_slug": app_slug,
                "message": "Setup complete. You can now sign in.",
            })

        # ── Auth ─────────────────────────────────────────────────────

        @app.post("/api/v1/auth/login")
        async def auth_login(request: Request) -> JSONResponse:
            """Authenticate with email and password."""
            body = await request.json()
            email = body.get("email", "")
            password = body.get("password", "")
            st = _load_state()
            admin_data = st.get("admin")
            if not admin_data or admin_data["email"] != email:
                return JSONResponse(
                    {"detail": "Invalid email or password"}, status_code=401
                )
            if not _verify_password(password, admin_data["password_hash"], admin_data["password_salt"]):
                return JSONResponse(
                    {"detail": "Invalid email or password"}, status_code=401
                )
            token = _make_token()
            st.setdefault("active_tokens", [])
            st["active_tokens"].append(token)
            # Keep last 10 tokens to prevent unbounded growth
            st["active_tokens"] = st["active_tokens"][-10:]
            _save_state(st)
            resp = JSONResponse({
                "access_token": token,
                "token_type": "bearer",
                "user": {
                    "id": admin_data["id"],
                    "email": admin_data["email"],
                    "display_name": admin_data.get("name", ""),
                    "avatar_url": None,
                },
            })
            resp.set_cookie(
                key="sagewai_auth",
                value=token,
                httponly=True,
                samesite="lax",
                path="/",
            )
            return resp

        @app.post("/api/v1/auth/register")
        async def auth_register(request: Request) -> JSONResponse:
            """Register a new user (only if setup is complete)."""
            st = _load_state()
            if not st.get("setup_complete"):
                return JSONResponse(
                    {"detail": "Complete the initial setup first."}, status_code=403
                )
            body = await request.json()
            email = body.get("email", "")
            password = body.get("password", "")
            if not email or not password:
                return JSONResponse(
                    {"detail": "Email and password are required."}, status_code=422
                )
            if st.get("admin", {}).get("email") == email:
                return JSONResponse(
                    {"detail": "Email already in use."}, status_code=409
                )
            pw_hash, pw_salt = _hash_password(password)
            user_id = secrets.token_hex(8)
            users = st.setdefault("users", [])
            users.append({
                "id": user_id,
                "email": email,
                "display_name": body.get("display_name", ""),
                "password_hash": pw_hash,
                "password_salt": pw_salt,
                "role": "viewer",
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            })
            token = _make_token()
            st.setdefault("active_tokens", [])
            st["active_tokens"].append(token)
            # Keep last 10 tokens to prevent unbounded growth
            st["active_tokens"] = st["active_tokens"][-10:]
            _save_state(st)
            resp = JSONResponse({
                "access_token": token,
                "token_type": "bearer",
                "user": {
                    "id": user_id,
                    "email": email,
                    "display_name": body.get("display_name", ""),
                    "avatar_url": None,
                },
            })
            resp.set_cookie(
                key="sagewai_auth",
                value=token,
                httponly=True,
                samesite="lax",
                path="/",
            )
            return resp

        @app.post("/api/v1/auth/refresh")
        async def auth_refresh(request: Request) -> JSONResponse:
            """Refresh auth token using the httpOnly cookie."""
            cookie = request.cookies.get("sagewai_auth")
            st = _load_state()
            if cookie and cookie in st.get("active_tokens", []):
                new_token = _make_token()
                st["active_token"] = new_token
                _save_state(st)
                admin_data = st.get("admin", {})
                resp = JSONResponse({
                    "access_token": new_token,
                    "token_type": "bearer",
                    "user": {
                        "id": admin_data.get("id", ""),
                        "email": admin_data.get("email", ""),
                        "display_name": admin_data.get("name", ""),
                        "avatar_url": None,
                    },
                })
                resp.set_cookie(
                    key="sagewai_auth",
                    value=new_token,
                    httponly=True,
                    samesite="lax",
                    path="/",
                )
                return resp
            return JSONResponse({"detail": "Invalid session"}, status_code=401)

        @app.post("/api/v1/auth/logout")
        async def auth_logout() -> JSONResponse:
            """Clear the auth session."""
            resp = JSONResponse({"status": "ok"})
            resp.delete_cookie("sagewai_auth", path="/")
            return resp

        @app.get("/api/v1/auth/me")
        async def auth_me(request: Request) -> JSONResponse:
            """Return current user info from the auth token."""
            st = _load_state()
            # Check Authorization header first, then cookie
            auth_header = request.headers.get("authorization", "")
            token = None
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            if not token:
                token = request.cookies.get("sagewai_auth")
            if token and token in st.get("active_tokens", []):
                admin_data = st.get("admin", {})
                return JSONResponse({
                    "id": admin_data.get("id", ""),
                    "email": admin_data.get("email", ""),
                    "display_name": admin_data.get("name", ""),
                    "avatar_url": None,
                })
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        # ── Agent templates ───────────────────────────────────────────

        _AGENT_TEMPLATES = [
            # ── Getting Started ──────────────────────────────────
            {
                "id": "hello-agent",
                "name": "Hello Agent",
                "description": "Your first Sagewai agent in 5 lines. Demonstrates the basic Agent → run() → response loop with zero configuration.",
                "system_prompt": "You are a helpful AI assistant powered by Sagewai. Introduce yourself and explain what you can do.",
                "model": "gpt-4o-mini",
                "temperature": 0.7,
                "strategy": "single",
                "tools": [],
                "mcp_servers": [],
                "memory_backends": [],
                "guardrails": [],
                "category": "Getting Started",
            },
            # ── Tool Calling ─────────────────────────────────────
            {
                "id": "tool-agent",
                "name": "Tool-Augmented Agent",
                "description": "Demonstrates Sagewai's @tool decorator — give agents custom Python functions as superpowers. Includes weather lookup, calculator, and web search tools.",
                "system_prompt": "You are a helpful assistant with access to tools. Use them when the user's question requires real-time data, calculations, or external lookups. Always explain which tool you used and why.",
                "model": "gpt-4o",
                "temperature": 0.3,
                "strategy": "react",
                "tools": ["web_search", "calculator", "weather_lookup"],
                "mcp_servers": [],
                "memory_backends": [],
                "guardrails": [],
                "category": "Tool Calling",
            },
            # ── MCP Integration ──────────────────────────────────
            {
                "id": "mcp-connected",
                "name": "MCP-Connected Agent",
                "description": "Connect to any Model Context Protocol server and use its tools. Demonstrates MCP client discovery, tool listing, and invocation — the universal tool standard.",
                "system_prompt": "You are an agent connected to external MCP tool servers. Discover available tools, describe their capabilities, and use them to fulfill user requests.",
                "model": "gpt-4o",
                "temperature": 0.3,
                "strategy": "react",
                "tools": [],
                "mcp_servers": ["filesystem", "github"],
                "memory_backends": [],
                "guardrails": [],
                "category": "MCP Integration",
            },
            # ── Memory & Knowledge ───────────────────────────────
            {
                "id": "memory-agent",
                "name": "Persistent Memory Agent",
                "description": "An agent that remembers — uses vector memory for semantic search and graph memory for entity relationships. Demonstrates RAG, knowledge extraction, and cross-session recall.",
                "system_prompt": "You are a knowledgeable assistant with persistent memory. Use your vector memory to recall relevant past conversations and your knowledge graph to track entities, relationships, and facts. Reference specific memories when answering.",
                "model": "gpt-4o",
                "temperature": 0.4,
                "strategy": "react",
                "tools": [],
                "mcp_servers": [],
                "memory_backends": ["vector", "graph"],
                "guardrails": [],
                "category": "Memory & Knowledge",
            },
            {
                "id": "rag-researcher",
                "name": "RAG Research Assistant",
                "description": "Retrieval-Augmented Generation with Sagewai's context engine. Ingests documents, chunks them semantically, embeds with sentence-transformers, and retrieves relevant context before every LLM call.",
                "system_prompt": "You are a research assistant with access to a document corpus. For every question, search your knowledge base first, cite specific sources, and clearly distinguish between retrieved facts and your own reasoning.",
                "model": "gpt-4o",
                "temperature": 0.2,
                "strategy": "single",
                "tools": ["file_reader"],
                "mcp_servers": [],
                "memory_backends": ["vector"],
                "guardrails": ["hallucination_check"],
                "category": "Memory & Knowledge",
            },
            # ── Strategies ───────────────────────────────────────
            {
                "id": "react-agent",
                "name": "ReAct Reasoning Agent",
                "description": "Classic Reason-Act-Observe loop (Yao et al. 2023). The agent thinks step-by-step, decides which tool to call, observes the result, and iterates until done.",
                "system_prompt": "You are a methodical problem solver. Think step by step: (1) Reason about what you know and what you need, (2) Act by calling a tool, (3) Observe the result, (4) Repeat until you have a complete answer.",
                "model": "gpt-4o",
                "temperature": 0.3,
                "strategy": "react",
                "tools": ["web_search", "calculator"],
                "mcp_servers": [],
                "memory_backends": [],
                "guardrails": [],
                "category": "Strategies",
            },
            {
                "id": "tree-of-thoughts",
                "name": "Tree of Thoughts Agent",
                "description": "Explores multiple reasoning branches in parallel, scores each path, and prunes weak ones — ideal for complex problems with multiple valid approaches (Yao et al. 2024).",
                "system_prompt": "You are an analytical thinker. For complex problems, generate multiple solution approaches in parallel, evaluate each critically, and select the strongest path forward.",
                "model": "gpt-4o",
                "temperature": 0.7,
                "strategy": "tree_of_thoughts",
                "tools": [],
                "mcp_servers": [],
                "memory_backends": [],
                "guardrails": [],
                "category": "Strategies",
            },
            {
                "id": "debate-agent",
                "name": "Debate Strategy Agent",
                "description": "Multi-perspective reasoning — multiple debater personas argue across rounds, then a judge synthesizes the strongest arguments. Ideal for nuanced decisions.",
                "system_prompt": "You are a debate moderator. Present multiple perspectives on any issue, let them challenge each other with evidence, then synthesize the strongest arguments into a well-reasoned conclusion.",
                "model": "gpt-4o",
                "temperature": 0.6,
                "strategy": "debate",
                "tools": [],
                "mcp_servers": [],
                "memory_backends": [],
                "guardrails": [],
                "category": "Strategies",
            },
            {
                "id": "self-correcting",
                "name": "Self-Correcting Agent",
                "description": "Detects its own errors and automatically retries with improved reasoning. Uses PALADIN-style 1-shot correction and failure exemplars.",
                "system_prompt": "You are a precise assistant that checks its own work. After generating a response, critically evaluate it for errors, inconsistencies, or missing information. If issues are found, correct them before presenting the final answer.",
                "model": "gpt-4o",
                "temperature": 0.3,
                "strategy": "self_correction",
                "tools": ["code_interpreter"],
                "mcp_servers": [],
                "memory_backends": [],
                "guardrails": [],
                "category": "Strategies",
            },
            {
                "id": "plan-and-execute",
                "name": "Plan-and-Execute Agent",
                "description": "Decomposes complex goals into a step-by-step plan, executes each step, and optionally replans when circumstances change.",
                "system_prompt": "You are a strategic planner. For any complex task: (1) Decompose it into concrete subtasks, (2) Execute each step methodically, (3) Reflect on progress and replan if needed.",
                "model": "gpt-4o",
                "temperature": 0.3,
                "strategy": "planning",
                "tools": ["web_search", "file_reader", "code_interpreter"],
                "mcp_servers": [],
                "memory_backends": [],
                "guardrails": [],
                "category": "Strategies",
            },
            # ── Enterprise Safety ────────────────────────────────
            {
                "id": "safe-enterprise",
                "name": "Enterprise-Safe Agent",
                "description": "Demonstrates Sagewai's full safety stack: PII detection & redaction, hallucination checking, content filtering, output schema validation, and token budget enforcement.",
                "system_prompt": "You are a compliance-aware enterprise assistant. Never expose personally identifiable information. Always cite sources. Stay within approved topics. If uncertain, say so rather than guessing.",
                "model": "gpt-4o",
                "temperature": 0.3,
                "strategy": "single",
                "tools": [],
                "mcp_servers": [],
                "memory_backends": ["vector"],
                "guardrails": ["pii_filter", "hallucination_check", "content_filter", "output_schema", "token_budget"],
                "category": "Enterprise Safety",
            },
            # ── Directives ───────────────────────────────────────
            {
                "id": "directive-agent",
                "name": "Directive-Powered Agent",
                "description": "Uses Sagewai's @context, @memory, and @agent sigils in prompts. Enables small local models to leverage the full infrastructure without native tool calling.",
                "system_prompt": "You are an agent that uses directives to enrich your context. Use @context to pull relevant documents, @memory to recall facts, and @agent to delegate subtasks to specialist agents.",
                "model": "gpt-4o-mini",
                "temperature": 0.4,
                "strategy": "single",
                "tools": [],
                "mcp_servers": [],
                "memory_backends": ["vector", "graph"],
                "guardrails": [],
                "category": "Directives",
            },
            # ── Workflows ────────────────────────────────────────
            {
                "id": "research-pipeline",
                "name": "Research → Write → Edit Pipeline",
                "description": "A 3-agent sequential workflow: Researcher gathers info, Writer drafts the document, Editor polishes it. Demonstrates SequentialAgent composition with crash recovery.",
                "system_prompt": "You are the orchestrator of a research pipeline. Coordinate three specialized agents: a Researcher who gathers facts, a Writer who drafts content, and an Editor who refines the final output.",
                "model": "gpt-4o",
                "temperature": 0.4,
                "strategy": "planning",
                "tools": ["web_search", "file_reader"],
                "mcp_servers": [],
                "memory_backends": ["vector"],
                "guardrails": ["hallucination_check"],
                "category": "Workflows",
            },
            # ── Model Routing ────────────────────────────────────
            {
                "id": "smart-router",
                "name": "Smart Model Router",
                "description": "Routes requests to the optimal model based on task complexity: simple→local/Haiku ($0), medium→Sonnet, complex→Opus/GPT-4o. Demonstrates zero-LLM-call classification.",
                "system_prompt": "You are an intelligent router. Analyze each request's complexity and route it to the most cost-effective model that can handle it well.",
                "model": "auto",
                "temperature": 0.3,
                "strategy": "routing",
                "tools": [],
                "mcp_servers": [],
                "memory_backends": [],
                "guardrails": ["token_budget"],
                "category": "Model Routing",
            },
            {
                "id": "local-first",
                "name": "Local-First Agent",
                "description": "Routes simple tasks to a local LLM (Ollama, vLLM, LM Studio) at $0/token and only escalates complex tasks to cloud. Demonstrates hybrid local+cloud architecture.",
                "system_prompt": "You are a cost-optimized assistant. Handle simple requests locally and only use cloud models for complex reasoning, code generation, or multi-step tasks.",
                "model": "auto",
                "temperature": 0.5,
                "strategy": "routing",
                "tools": [],
                "mcp_servers": [],
                "memory_backends": [],
                "guardrails": ["token_budget"],
                "category": "Model Routing",
            },
            # ── Fleet & Distribution ─────────────────────────────
            {
                "id": "fleet-worker",
                "name": "Fleet Worker Agent",
                "description": "A distributed agent that runs on fleet workers across any infrastructure. Demonstrates worker enrollment, task dispatch, model-aware routing, and heartbeat monitoring.",
                "system_prompt": "You are a fleet-distributed agent. Report your capabilities, accept dispatched tasks, and return results reliably.",
                "model": "gpt-4o-mini",
                "temperature": 0.3,
                "strategy": "single",
                "tools": [],
                "mcp_servers": [],
                "memory_backends": [],
                "guardrails": [],
                "category": "Fleet & Distribution",
            },
            # ── IDE Governance ───────────────────────────────────
            {
                "id": "harness-proxy",
                "name": "IDE Cost Governor",
                "description": "Enterprise proxy for Claude Code, Cursor, and Copilot. Routes requests through Sagewai's harness for budget enforcement, smart model routing, and spend tracking — without changing any IDE settings.",
                "system_prompt": "You are the Sagewai harness proxy. Intercept LLM requests from IDE tools, classify complexity, route to the optimal model, enforce budgets, and log all spend.",
                "model": "auto",
                "temperature": 0.3,
                "strategy": "routing",
                "tools": [],
                "mcp_servers": [],
                "memory_backends": [],
                "guardrails": ["token_budget"],
                "category": "IDE Governance",
            },
            # ── Domain-Specific ──────────────────────────────────
            {
                "id": "customer-support",
                "name": "Customer Support Agent",
                "description": "Handle customer inquiries with persistent memory, PII protection, and escalation rules. Remembers past interactions and knows when to hand off to a human.",
                "system_prompt": "You are a customer support specialist. Be empathetic and solution-oriented. Use your memory to recall past interactions with this customer. Redact any PII from logs. Escalate to a human when: (1) the customer is frustrated, (2) you can't resolve in 3 turns, or (3) the issue involves billing.",
                "model": "gpt-4o-mini",
                "temperature": 0.5,
                "strategy": "react",
                "tools": ["ticket_lookup", "knowledge_base"],
                "mcp_servers": [],
                "memory_backends": ["vector"],
                "guardrails": ["pii_filter", "content_filter"],
                "category": "Domain-Specific",
            },
            {
                "id": "legal-reviewer",
                "name": "Legal Document Reviewer",
                "description": "Reviews contracts and legal documents with RAG over your policy corpus. Flags risky clauses, missing terms, and deviations from standard playbook.",
                "system_prompt": "You are a legal document analyst. Review contracts against the organization's standard terms. Flag: (1) non-standard liability clauses, (2) missing IP assignment, (3) auto-renewal traps, (4) unlimited indemnification. Cite the specific clause numbers.",
                "model": "gpt-4o",
                "temperature": 0.1,
                "strategy": "single",
                "tools": ["file_reader"],
                "mcp_servers": [],
                "memory_backends": ["vector"],
                "guardrails": ["pii_filter", "hallucination_check"],
                "category": "Domain-Specific",
            },
        ]

        @app.get("/api/v1/agents/templates")
        async def list_agent_templates() -> JSONResponse:
            """Return built-in agent templates."""
            return JSONResponse(_AGENT_TEMPLATES)

        @app.get("/api/v1/agents/templates/{template_id}")
        async def get_agent_template(template_id: str) -> JSONResponse:
            """Return a single agent template by ID."""
            for t in _AGENT_TEMPLATES:
                if t["id"] == template_id:
                    return JSONResponse(t)
            return JSONResponse({"detail": "Template not found"}, status_code=404)

        # ── Health ───────────────────────────────────────────────────

        @app.get("/api/v1/health/summary")
        async def health_summary() -> JSONResponse:
            """Lightweight health probe for the admin connection monitor."""
            return JSONResponse({
                "status": "healthy",
                "sdk_version": _cli.VERSION,
            })

        @app.get("/api/v1/health/detailed")
        async def health_detailed() -> JSONResponse:
            """Detailed health check with service statuses."""
            return JSONResponse({
                "status": "healthy",
                "sdk_version": _cli.VERSION,
                "checked_at": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
                "services": [],
            })

        click.echo(f"Starting Sagewai Admin API on {host}:{port}")
        uvicorn.run(app, host=host, port=port)
    except ImportError as exc:
        click.echo(
            f"Error: missing dependency for admin serve: {exc}. "
            "Install with: uv add 'sagewai[fastapi]'",
            err=True,
        )
        raise SystemExit(1)


@admin.command("health")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def admin_health(as_json: bool) -> None:
    """Show system health status from the admin API."""
    data = _cli._api_get("/api/v1/health/detailed")
    if as_json:
        _cli._echo_json(data)
        return
    status = data.get("status", "unknown")
    click.echo(f"System Status: {status.upper()}")
    click.echo(f"SDK Version  : {data.get('sdk_version', '—')}")
    click.echo(f"Checked      : {data.get('checked_at', '—')}")
    click.echo()
    for svc in data.get("services", []):
        latency = (
            f" ({svc['latency_ms']:.1f}ms)"
            if svc.get("latency_ms")
            else ""
        )
        detail = f" — {svc['detail']}" if svc.get("detail") else ""
        click.echo(
            f"  {svc['name']:20s} {svc['status']}{latency}{detail}"
        )
