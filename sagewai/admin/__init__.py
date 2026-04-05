# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Admin API — agent registry, run history, session inspection, and run controls.

Provides a FastAPI router for management and observability of agent systems.
Mount into any FastAPI app to get admin endpoints.

Usage::

    from fastapi import FastAPI
    from sagewai.admin import create_admin_router, AdminState, RunControlRegistry

    state = AdminState()
    run_controls = RunControlRegistry()
    app = FastAPI()
    app.include_router(
        create_admin_router(state, run_controls=run_controls), prefix="/admin"
    )
"""

from sagewai.admin.budget import BudgetCheckResult, BudgetLimit, BudgetManager
from sagewai.admin.controller import (
    AgentCancelledError,
    RunController,
    RunControlRegistry,
)
from sagewai.admin.health import AgentHealthMonitor, AgentHealthState, HealthConfig
from sagewai.admin.models import (
    AgentDetail,
    AgentSummary,
    ConfigUpdateRequest,
    ControlActionResponse,
    HealthSnapshot,
    RunDetail,
    RunSummary,
    SessionInfo,
)
from sagewai.admin.postgres_analytics import PostgresAnalyticsStore
from sagewai.admin.postgres_budget import PostgresBudgetManager
from sagewai.admin.postgres_guardrails import PostgresGuardrailStore
from sagewai.admin.state import AdminState
from sagewai.admin.store import RunStore

from sagewai.admin.analytics import AnalyticsStore

# Lazy imports — these modules require FastAPI (optional dependency).
# Import them only when accessed to allow ``import sagewai`` without fastapi.
create_admin_router = None
create_analytics_router = None
try:
    from sagewai.admin.analytics import create_analytics_router
    from sagewai.admin.api import create_admin_router
except ImportError:  # pragma: no cover — fastapi not installed
    pass

__all__ = [
    "AdminState",
    "AnalyticsStore",
    "BudgetCheckResult",
    "BudgetLimit",
    "BudgetManager",
    "AgentCancelledError",
    "AgentDetail",
    "AgentHealthMonitor",
    "AgentHealthState",
    "AgentSummary",
    "ConfigUpdateRequest",
    "ControlActionResponse",
    "HealthConfig",
    "HealthSnapshot",
    "PostgresAnalyticsStore",
    "PostgresBudgetManager",
    "PostgresGuardrailStore",
    "RunControlRegistry",
    "RunController",
    "RunDetail",
    "RunStore",
    "RunSummary",
    "SessionInfo",
    "create_admin_router",
    "create_analytics_router",
]
