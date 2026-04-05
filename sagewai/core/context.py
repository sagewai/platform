# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Project-scoped isolation — ProjectContext with namespace isolation, quotas, and rate limiting."""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Context variable for propagating project through async call chains
_current_project: ContextVar[ProjectContext | None] = ContextVar("_current_project", default=None)


class ProjectQuota(BaseModel):
    """Resource quotas for a project."""

    max_tokens_per_minute: int = Field(default=100_000, gt=0)
    max_requests_per_minute: int = Field(default=60, gt=0)
    max_cost_per_day_usd: float = Field(default=100.0, gt=0)


class ProjectUsage(BaseModel):
    """Tracks current usage for quota enforcement."""

    tokens_used: int = 0
    requests_made: int = 0
    cost_usd: float = 0.0
    window_start: float = Field(default_factory=time.time)
    day_start: float = Field(default_factory=time.time)


class ProjectContext(BaseModel):
    """Project context with namespace isolation, quotas, and rate limiting.

    Usage::

        ctx = ProjectContext(project_id="acme-corp")
        with ctx:
            result = await agent.chat("hello")

    All memory stores and resource access within the context are scoped
    to the project's namespace.
    """

    project_id: str = Field(..., min_length=1, description="Unique project identifier")
    namespace: str = Field(default="", description="Namespace prefix for memory stores; defaults to project_id")
    quota: ProjectQuota = Field(default_factory=ProjectQuota)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _usage: ProjectUsage | None = None

    model_config = {"arbitrary_types_allowed": True}

    def model_post_init(self, __context: Any) -> None:
        if not self.namespace:
            self.namespace = self.project_id
        self._usage = ProjectUsage()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> ProjectContext:
        self._token = _current_project.set(self)
        return self

    def __exit__(self, *args: Any) -> None:
        _current_project.reset(self._token)

    async def __aenter__(self) -> ProjectContext:
        self._token = _current_project.set(self)
        return self

    async def __aexit__(self, *args: Any) -> None:
        _current_project.reset(self._token)

    # ------------------------------------------------------------------
    # Namespace helpers
    # ------------------------------------------------------------------

    def scoped_collection(self, base_name: str) -> str:
        """Return a namespace-scoped collection name for memory stores.

        Example::

            ctx = ProjectContext(project_id="acme")
            ctx.scoped_collection("documents")  # → "acme__documents"
        """
        return f"{self.namespace}__{base_name}"

    def scoped_key(self, key: str) -> str:
        """Return a namespace-scoped cache/store key."""
        return f"{self.namespace}:{key}"

    # ------------------------------------------------------------------
    # Quota enforcement
    # ------------------------------------------------------------------

    def check_rate_limit(self) -> None:
        """Raise if project has exceeded per-minute request or token quota.

        Automatically resets the window after 60 seconds.
        """
        usage = self._usage
        assert usage is not None
        now = time.time()

        # Reset per-minute window
        if now - usage.window_start >= 60:
            usage.tokens_used = 0
            usage.requests_made = 0
            usage.window_start = now

        if usage.requests_made >= self.quota.max_requests_per_minute:
            raise ProjectRateLimitError(
                f"Project {self.project_id} exceeded {self.quota.max_requests_per_minute} requests/min"
            )

    def check_token_quota(self, tokens: int) -> None:
        """Raise if adding *tokens* would exceed the per-minute token quota."""
        usage = self._usage
        assert usage is not None
        now = time.time()

        if now - usage.window_start >= 60:
            usage.tokens_used = 0
            usage.requests_made = 0
            usage.window_start = now

        if usage.tokens_used + tokens > self.quota.max_tokens_per_minute:
            raise ProjectQuotaExceededError(
                f"Project {self.project_id} would exceed {self.quota.max_tokens_per_minute} tokens/min"
            )

    def check_cost_quota(self, cost: float) -> None:
        """Raise if adding *cost* would exceed the daily cost quota."""
        usage = self._usage
        assert usage is not None
        now = time.time()

        # Reset daily window
        if now - usage.day_start >= 86400:
            usage.cost_usd = 0.0
            usage.day_start = now

        if usage.cost_usd + cost > self.quota.max_cost_per_day_usd:
            raise ProjectQuotaExceededError(
                f"Project {self.project_id} would exceed ${self.quota.max_cost_per_day_usd}/day"
            )

    def record_usage(self, tokens: int = 0, cost: float = 0.0) -> None:
        """Record usage after a successful API call."""
        usage = self._usage
        assert usage is not None
        usage.tokens_used += tokens
        usage.requests_made += 1
        usage.cost_usd += cost

    @property
    def usage(self) -> ProjectUsage:
        """Return the current usage snapshot."""
        assert self._usage is not None
        return self._usage


# ------------------------------------------------------------------
# Module-level accessor
# ------------------------------------------------------------------


def get_current_project() -> ProjectContext | None:
    """Return the project context for the current async task, or None."""
    return _current_project.get()


def resolve_project_id(project_id: str | None = None) -> str:
    """Resolve a project_id from an explicit argument, contextvar, or fallback.

    Parameters
    ----------
    project_id:
        If provided (non-None, non-empty), returned as-is.

    Returns
    -------
    str
        The resolved project identifier. Falls back to ``"default"`` when
        no explicit value and no ``ProjectContext`` is active.
    """
    if project_id:
        return project_id
    ctx = _current_project.get()
    return ctx.project_id if ctx else "default"


def require_project() -> ProjectContext:
    """Return the current project or raise if none is set."""
    ctx = _current_project.get()
    if ctx is None:
        raise ProjectRequiredError("No project context — wrap call in ProjectContext()")
    return ctx


# ------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------


class ProjectError(Exception):
    """Base class for project-related errors."""


class ProjectRequiredError(ProjectError):
    """Raised when a project context is required but not found."""


class ProjectRateLimitError(ProjectError):
    """Raised when a project exceeds their rate limit."""


class ProjectQuotaExceededError(ProjectError):
    """Raised when a project exceeds their resource quota."""
