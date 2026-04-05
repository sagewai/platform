# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tool specification and result types."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import BaseModel, Field

# Python type → JSON Schema type mapping
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


class ToolSpec(BaseModel):
    """Specification for a tool an agent can invoke."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )
    handler: Callable[..., Any] | None = Field(default=None, exclude=True)

    model_config = {"arbitrary_types_allowed": True}


class ToolResult(BaseModel):
    """Result of executing a tool."""

    tool_call_id: str
    name: str
    content: str
    error: str | None = None


def _python_type_to_json_schema(tp: type) -> dict[str, str]:
    """Convert a Python type annotation to a JSON Schema type."""
    origin = getattr(tp, "__origin__", None)
    if origin is list:
        return {"type": "array"}
    if origin is dict:
        return {"type": "object"}

    # Handle Optional (Union[X, None])
    if origin is type(int | None):
        type_args = getattr(tp, "__args__", ())
        args = [a for a in type_args if a is not type(None)]
        if args:
            return _python_type_to_json_schema(args[0])

    return {"type": _TYPE_MAP.get(tp, "string")}


def tool(fn: Callable[..., Any]) -> ToolSpec:
    """Convert a typed Python function into a ToolSpec with auto-generated JSON Schema.

    Usage:
        @tool
        async def get_weather(city: str, units: str = "celsius") -> str:
            \"\"\"Get current weather for a city.\"\"\"
            ...

        agent = UniversalAgent(name="bot", model="gpt-4o", tools=[get_weather])
    """
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)
    doc = inspect.getdoc(fn) or fn.__name__

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        param_type = hints.get(param_name, str)
        schema = _python_type_to_json_schema(param_type)
        properties[param_name] = schema

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    parameters = {
        "type": "object",
        "properties": properties,
        "required": required,
    }

    spec = ToolSpec(
        name=fn.__name__,
        description=doc,
        parameters=parameters,
        handler=fn,
    )
    # Preserve the original function for direct calling
    spec._original_fn = fn  # type: ignore[attr-defined]
    return spec
