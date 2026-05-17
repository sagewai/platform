# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The transform engine — runs one request, never raises."""

from __future__ import annotations

import asyncio
import logging

from sagewai.transform.models import TransformRequest, TransformResult
from sagewai.transform.registry import TransformRegistry

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class TransformEngine:
    """Resolves a :class:`TransformRequest` against a registry and runs it.

    Always returns a :class:`TransformResult` — unknown operations, operation
    failures, and timeouts all surface as ``ok=False`` results rather than
    exceptions, so a transform can never crash the agent loop.
    """

    def __init__(
        self, registry: TransformRegistry, *, timeout: float = _DEFAULT_TIMEOUT
    ) -> None:
        self._registry = registry
        self._timeout = timeout

    async def run(self, request: TransformRequest) -> TransformResult:
        """Run ``request`` and return a :class:`TransformResult`."""
        try:
            op = self._registry.get(request.operation)
        except KeyError:
            return TransformResult(
                operation=request.operation,
                output="",
                ok=False,
                error=f"unknown transform operation: {request.operation}",
            )

        try:
            # asyncio.wait_for (not asyncio.timeout) — CI runs Python 3.10.
            result = await asyncio.wait_for(
                op(request.content, project_id=request.project_id, **request.params),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            error = f"transform operation timed out after {self._timeout}s"
            logger.warning("transform operation %r %s", request.operation, error)
            return TransformResult(
                operation=request.operation, output="", ok=False, error=error
            )
        except Exception as exc:  # noqa: BLE001 — a transform must never crash the agent
            logger.warning(
                "transform operation %r failed: %s", request.operation, exc
            )
            return TransformResult(
                operation=request.operation,
                output="",
                ok=False,
                error=str(exc) or exc.__class__.__name__,
            )

        if isinstance(result, TransformResult):
            return result
        return TransformResult(
            operation=request.operation, output=str(result), ok=True
        )
