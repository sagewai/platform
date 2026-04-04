# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Standalone FastAPI application factory for the LLM Harness.

Creates a self-contained app with both proxy and admin routes, suitable
for running as an independent service or embedding in a larger app.

Usage::

    from sagewai.harness.app import create_harness_app

    app = create_harness_app(
        anthropic_api_key="sk-ant-...",
        openai_api_key="sk-...",
    )

Run with::

    uvicorn sagewai.harness.app:app --host 0.0.0.0 --port 8100
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sagewai.admin.budget import BudgetManager
from sagewai.harness.admin_api import create_harness_admin_router
from sagewai.harness.api import create_harness_proxy_router
from sagewai.harness.backend import (
    AnthropicBackend,
    LiteLLMProxyBackend,
    LLMBackend,
    OpenAIBackend,
)
from sagewai.harness.budget import HarnessBudgetManager
from sagewai.harness.classifier import ClassifierThresholds, RequestClassifier
from sagewai.harness.models import HarnessConfig, ModelTierConfig
from sagewai.harness.policy import PolicyEngine
from sagewai.harness.proxy import HarnessProxy
from sagewai.harness.router import HarnessRouter
from sagewai.harness.store import InMemoryHarnessStore

logger = logging.getLogger(__name__)


def create_harness_app(
    *,
    anthropic_api_key: str = "",
    openai_api_key: str = "",
    litellm_proxy_url: str = "",
    litellm_proxy_key: str = "",
    backends: dict[str, LLMBackend] | None = None,
    config: HarnessConfig | None = None,
    tier_config: ModelTierConfig | None = None,
    classifier_thresholds: ClassifierThresholds | None = None,
    store: InMemoryHarnessStore | None = None,
    cors_origins: list[str] | None = None,
    title: str = "Sagewai LLM Harness",
    version: str = "0.1.0",
) -> FastAPI:
    """Create a standalone FastAPI app for the LLM Harness.

    Wires up proxy endpoints (Anthropic + OpenAI formats), admin CRUD
    endpoints, and all internal components (classifier, router, policy
    engine, budget manager).

    Args:
        anthropic_api_key: Anthropic API key for the Anthropic backend.
        openai_api_key: OpenAI API key for the OpenAI backend.
        litellm_proxy_url: URL of a LiteLLM proxy instance.
        litellm_proxy_key: API key for the LiteLLM proxy.
        backends: Pre-configured backends (overrides auto-detection
            from API keys). Map of provider name to backend instance.
        config: Global harness configuration. Defaults are used if
            not provided.
        tier_config: Model tier configuration override.
        classifier_thresholds: Custom classifier thresholds.
        store: Harness store instance. Defaults to in-memory.
        cors_origins: Allowed CORS origins. Defaults to ``["*"]``.
        title: App title for OpenAPI docs.
        version: App version for OpenAPI docs.

    Returns:
        A fully configured FastAPI application.
    """
    harness_config = config or HarnessConfig()
    harness_tier_config = tier_config or harness_config.default_tier_config
    harness_store = store or InMemoryHarnessStore()

    # ── Build backends ───────────────────────────────────────────
    resolved_backends: dict[str, LLMBackend] = {}
    if backends:
        resolved_backends.update(backends)
    else:
        if anthropic_api_key:
            resolved_backends["anthropic"] = AnthropicBackend(
                api_key=anthropic_api_key,
            )
        if openai_api_key:
            resolved_backends["openai"] = OpenAIBackend(
                api_key=openai_api_key,
            )
        if litellm_proxy_url:
            resolved_backends["litellm"] = LiteLLMProxyBackend(
                proxy_url=litellm_proxy_url,
                api_key=litellm_proxy_key,
            )
        # Set default backend fallback.
        if "default" not in resolved_backends:
            if "anthropic" in resolved_backends:
                resolved_backends["default"] = resolved_backends["anthropic"]
            elif "openai" in resolved_backends:
                resolved_backends["default"] = resolved_backends["openai"]
            elif "litellm" in resolved_backends:
                resolved_backends["default"] = resolved_backends["litellm"]

    # ── Build internal components ────────────────────────────────
    classifier = RequestClassifier(thresholds=classifier_thresholds)
    policy_engine = PolicyEngine(store=harness_store)
    budget_manager = HarnessBudgetManager(BudgetManager())

    harness_router = HarnessRouter(
        classifier=classifier,
        policy_engine=policy_engine,
        budget_manager=budget_manager,
        tier_config=harness_tier_config,
        allow_override=harness_config.allow_model_override,
    )

    proxy = HarnessProxy(
        store=harness_store,
        router=harness_router,
        backends=resolved_backends,
        config=harness_config,
    )

    # ── Lifespan ─────────────────────────────────────────────────

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "LLM Harness starting — %d backend(s) configured: %s",
            len(resolved_backends),
            ", ".join(resolved_backends.keys()),
        )
        yield
        logger.info("LLM Harness shutting down")

    # ── App assembly ─────────────────────────────────────────────

    app = FastAPI(
        title=title,
        version=version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS
    origins = cors_origins or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    proxy_router = create_harness_proxy_router(proxy)
    admin_router = create_harness_admin_router(
        store=harness_store,
        classifier=classifier,
        config=harness_config,
    )

    app.include_router(proxy_router)
    app.include_router(admin_router, prefix="/api/v1/harness")

    # Expose internal state for external access (e.g., seeding demo data)
    app.state.harness_store = harness_store
    app.state.harness_config = harness_config
    app.state.harness_proxy = proxy

    # Health endpoint
    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "healthy",
            "backends": list(resolved_backends.keys()),
            "config_enabled": harness_config.enabled,
        }

    return app
