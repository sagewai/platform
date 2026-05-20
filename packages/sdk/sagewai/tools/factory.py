# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Adapter from :class:`CatalogEntry` to the autopilot ``ToolCallable`` shape.

The autopilot's ``ToolRunner`` expects ``Callable[[dict], Awaitable[dict]]``.
Each catalog entry becomes one such callable that closes over its executor
and credential accessor. Operation selection is passed in-band via the
``_operation`` key on the input dict; the factory strips it before
forwarding to the executor.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from sagewai.tools import executors, registry
from sagewai.tools.registry import CatalogEntry


def _executor_for(kind: str):
    return executors.get(kind)


def _make_callable(
    entry: CatalogEntry,
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def _call(payload: dict[str, Any]) -> dict[str, Any]:
        # Resolve at call time so monkeypatching executors._REGISTRY in
        # tests is respected. Build-time resolution would cache the
        # original callable.
        runner = _executor_for(entry.kind)
        op = payload.pop("_operation", None) if isinstance(payload, dict) else None
        return await runner(
            entry,
            operation=op,
            inputs=payload,
            project_id=project_id,
            get_credentials=get_credentials,
        )

    _call.__name__ = f"tool_{entry.id}"
    return _call


def build_callables(
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]:
    """Build the ``dict[tool_id, callable]`` the ToolRunner consumes."""
    if not registry._loaded:
        registry.load()
    return {
        entry.id: _make_callable(entry, project_id=project_id, get_credentials=get_credentials)
        for entry in registry._entries.values()
    }


__all__ = ["build_callables"]
