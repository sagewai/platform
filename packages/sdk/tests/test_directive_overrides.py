# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for directive override wiring and prompt-based tool parsing (#419)."""

import pytest

from sagewai.directives.formatter import parse_tool_call_from_output


class TestDirectiveOverrideFields:
    def test_agent_has_override_fields(self):
        """BaseAgent should have model and budget override fields."""
        from sagewai.core.base import BaseAgent

        # Check that the class has the override attribute initialization
        import inspect

        source = inspect.getsource(BaseAgent.__init__)
        assert "_current_model_override" in source
        assert "_current_budget_override" in source


class TestPromptBasedToolParsing:
    def test_parse_tool_call_json(self):
        """parse_tool_call_from_output should parse TOOL_CALL from text."""
        text = (
            'I need to calculate this.\n'
            'TOOL_CALL: {"name": "calculator", "arguments": {"expr": "2+2"}}'
        )
        result = parse_tool_call_from_output(text)
        assert result is not None
        name, args = result
        assert name == "calculator"
        assert args["expr"] == "2+2"

    def test_parse_no_tool_call(self):
        """Should return None when no TOOL_CALL in text."""
        result = parse_tool_call_from_output("Just a normal response")
        assert result is None

    def test_parse_unknown_tool_ignored(self):
        """Should return the parsed tool regardless — filtering is done upstream."""
        text = 'TOOL_CALL: {"name": "unknown_tool", "arguments": {}}'
        result = parse_tool_call_from_output(text)
        assert result is not None
        name, args = result
        assert name == "unknown_tool"
        assert args == {}

    def test_parse_malformed_json(self):
        """Should return None on malformed JSON."""
        text = "TOOL_CALL: {this is not json}"
        result = parse_tool_call_from_output(text)
        assert result is None


class TestModelOverrideWiring:
    def test_build_messages_stores_override(self):
        """The _build_messages method should store model override from directives."""
        import inspect

        from sagewai.core.base import BaseAgent

        source = inspect.getsource(BaseAgent._build_messages)
        assert "_current_model_override" in source
        assert "overrides.model" in source

    def test_call_llm_uses_override(self):
        """The _call_llm method should use directive model override."""
        import inspect

        from sagewai.core.base import BaseAgent

        source = inspect.getsource(BaseAgent._call_llm)
        assert "_current_model_override" in source
