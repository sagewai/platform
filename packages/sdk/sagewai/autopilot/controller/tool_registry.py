# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""ToolRegistry — in-memory map of tool names to callables and JSON schemas.

Usage::

    registry = ToolRegistry()
    registry.register(
        name="get_metrics",
        description="Fetch current service metrics.",
        parameters={"type": "object", "properties": {...}, "required": [...]},
        callable_=my_async_fn,
    )
    tool_spec_list = registry.specs_for(("get_metrics",))
    result = await registry.execute("get_metrics", {"service": "api"})
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class ToolSpec(BaseModel):
    """Descriptor passed to the LLM's tool-calling API.

    Attributes:
        name: Unique tool name (matches the ``function.name`` in the
            OpenAI/Anthropic tool-calling wire format).
        description: Natural-language explanation shown to the model.
        parameters: JSON Schema object describing the function arguments.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        """Convert to the OpenAI ``tools`` list entry format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class _ToolEntry:
    """Internal storage entry holding spec + callable."""

    __slots__ = ("spec", "callable_")

    def __init__(self, spec: ToolSpec, callable_: Callable[..., Any]) -> None:
        self.spec = spec
        self.callable_ = callable_


class ToolRegistry:
    """In-memory registry mapping tool names to specs and callables.

    Tools can be registered via :meth:`register` and looked up via
    :meth:`specs_for` (for the LLM API call) or executed via
    :meth:`execute` (after the LLM returns tool_calls).

    Both sync and async callables are accepted. Sync callables are
    executed via :func:`asyncio.to_thread` to avoid blocking the event
    loop.
    """

    def __init__(self) -> None:
        self._tools: dict[str, _ToolEntry] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        callable_: Callable[..., Any],
    ) -> None:
        """Register a callable under *name*.

        Args:
            name: Unique tool name. Re-registering the same name
                silently replaces the previous entry.
            description: Human-readable tool description passed to the
                LLM.
            parameters: JSON Schema ``object`` describing the callable's
                arguments.
            callable_: The function to invoke. May be async or sync.
        """
        spec = ToolSpec(name=name, description=description, parameters=parameters)
        self._tools[name] = _ToolEntry(spec=spec, callable_=callable_)
        logger.debug("ToolRegistry: registered tool %r", name)

    def specs_for(self, names: tuple[str, ...]) -> list[dict[str, Any]]:
        """Return the OpenAI-format tool specs for *names*.

        Args:
            names: Tuple of tool names to look up.

        Returns:
            List of OpenAI ``tools`` list entries (one per name).

        Raises:
            KeyError: If any name in *names* is not registered.
        """
        result: list[dict[str, Any]] = []
        for name in names:
            if name not in self._tools:
                raise KeyError(
                    f"Tool {name!r} is not registered in ToolRegistry. "
                    f"Available: {sorted(self._tools)}"
                )
            result.append(self._tools[name].spec.to_openai())
        return result

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute the callable registered under *name* with *arguments*.

        Args:
            name: The tool name to execute.
            arguments: Keyword arguments parsed from the LLM's
                ``tool_calls[i].function.arguments`` JSON string.

        Returns:
            Whatever the tool callable returns.

        Raises:
            KeyError: If *name* is not registered.
        """
        if name not in self._tools:
            raise KeyError(
                f"Tool {name!r} is not registered in ToolRegistry. "
                f"Available: {sorted(self._tools)}"
            )
        entry = self._tools[name]
        fn = entry.callable_
        if inspect.iscoroutinefunction(fn):
            return await fn(**arguments)
        return await asyncio.to_thread(fn, **arguments)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools
