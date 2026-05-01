# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Extended tests for the @tool decorator — edge cases, handler invocation, complex types."""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from sagewai.models.tool import ToolSpec, ToolResult, tool


class TestToolDecoratorEdgeCases:
    """Test @tool decorator with edge cases and complex signatures."""

    def test_tool_without_docstring(self) -> None:
        """Function without a docstring should use __name__ as description."""

        @tool
        def no_docs(x: int) -> str:
            return str(x)

        assert isinstance(no_docs, ToolSpec)
        assert no_docs.name == "no_docs"
        # Description should fallback to function name or empty
        assert no_docs.description is not None

    def test_tool_with_no_params(self) -> None:
        """Function with no parameters should produce empty properties."""

        @tool
        def noop() -> str:
            """Does nothing."""
            return "done"

        assert isinstance(noop, ToolSpec)
        assert noop.name == "noop"
        props = noop.parameters.get("properties", {})
        assert len(props) == 0

    def test_tool_with_defaults(self) -> None:
        """Default values should not appear in required list."""

        @tool
        def greet(name: str, greeting: str = "Hello") -> str:
            """Greet someone."""
            return f"{greeting}, {name}!"

        assert isinstance(greet, ToolSpec)
        required = greet.parameters.get("required", [])
        assert "name" in required
        assert "greeting" not in required

    def test_tool_with_optional_type(self) -> None:
        """Optional[T] should not be required."""

        @tool
        def maybe(value: Optional[int] = None) -> str:
            """Maybe do something."""
            return str(value)

        assert isinstance(maybe, ToolSpec)
        required = maybe.parameters.get("required", [])
        assert "value" not in required

    def test_tool_with_bool_param(self) -> None:
        """Boolean parameter should map to JSON Schema boolean."""

        @tool
        def toggle(enabled: bool) -> str:
            """Toggle something."""
            return str(enabled)

        props = toggle.parameters.get("properties", {})
        assert props["enabled"]["type"] == "boolean"

    def test_tool_with_list_param(self) -> None:
        """List parameter should map to JSON Schema array."""

        @tool
        def batch(items: list) -> str:
            """Process items."""
            return str(len(items))

        props = batch.parameters.get("properties", {})
        assert props["items"]["type"] == "array"

    def test_tool_with_dict_param(self) -> None:
        """Dict parameter should map to JSON Schema object."""

        @tool
        def configure(settings: dict) -> str:
            """Apply settings."""
            return "ok"

        props = configure.parameters.get("properties", {})
        assert props["settings"]["type"] == "object"

    def test_tool_preserves_original_fn(self) -> None:
        """The original function should be accessible via _original_fn."""

        def my_func(x: int) -> int:
            """Double it."""
            return x * 2

        spec = tool(my_func)
        assert hasattr(spec, "_original_fn")
        assert spec._original_fn is my_func

    def test_tool_handler_is_callable(self) -> None:
        """The handler should be set and callable."""

        @tool
        def adder(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        assert adder.handler is not None
        assert callable(adder.handler)

    def test_tool_handler_invocation(self) -> None:
        """The handler should execute the original function."""

        @tool
        def multiply(x: int, y: int) -> int:
            """Multiply."""
            return x * y

        # Handler receives JSON string args, returns string
        result = multiply.handler(x=3, y=4)
        assert result == 12

    def test_async_tool(self) -> None:
        """Async functions should produce a valid ToolSpec."""

        @tool
        async def fetch(url: str) -> str:
            """Fetch a URL."""
            return f"fetched {url}"

        assert isinstance(fetch, ToolSpec)
        assert fetch.name == "fetch"
        assert fetch.handler is not None

    def test_tool_multiple_string_params(self) -> None:
        """Multiple string params should all be typed correctly."""

        @tool
        def search(query: str, category: str, lang: str = "en") -> str:
            """Search for something."""
            return f"{query} in {category} ({lang})"

        props = search.parameters.get("properties", {})
        assert props["query"]["type"] == "string"
        assert props["category"]["type"] == "string"
        assert props["lang"]["type"] == "string"
        required = search.parameters.get("required", [])
        assert "query" in required
        assert "category" in required
        assert "lang" not in required

    def test_tool_float_param(self) -> None:
        """Float parameter should map to JSON Schema number."""

        @tool
        def scale(factor: float) -> str:
            """Scale something."""
            return str(factor)

        props = scale.parameters.get("properties", {})
        assert props["factor"]["type"] == "number"


class TestToolSpec:
    """Test ToolSpec model behavior."""

    def test_toolspec_serialization_excludes_handler(self) -> None:
        """Handler should not appear in serialized output."""
        spec = ToolSpec(
            name="test",
            description="A test tool",
            parameters={"type": "object", "properties": {}},
            handler=lambda: None,
        )
        data = spec.model_dump()
        assert "handler" not in data

    def test_toolspec_json_roundtrip(self) -> None:
        """ToolSpec should survive JSON serialization."""
        spec = ToolSpec(
            name="roundtrip",
            description="Test roundtrip",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
        )
        json_str = spec.model_dump_json()
        restored = ToolSpec.model_validate_json(json_str)
        assert restored.name == "roundtrip"
        assert restored.parameters["required"] == ["x"]


class TestToolResult:
    """Test ToolResult model."""

    def test_tool_result_success(self) -> None:
        result = ToolResult(
            tool_call_id="tc-1",
            name="search",
            content="Found 5 results",
        )
        assert result.error is None

    def test_tool_result_error(self) -> None:
        result = ToolResult(
            tool_call_id="tc-2",
            name="search",
            content="",
            error="Connection timeout",
        )
        assert result.error == "Connection timeout"

    def test_tool_result_serialization(self) -> None:
        result = ToolResult(
            tool_call_id="tc-3",
            name="calc",
            content="42",
        )
        data = result.model_dump()
        assert data["tool_call_id"] == "tc-3"
        assert data["content"] == "42"
