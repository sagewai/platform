# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for dynamic parameter resolution in directives (#445)."""

import re
from unittest.mock import patch

import pytest

from sagewai.directives.parameters import resolve_backtick_params, resolve_parameters
from sagewai.directives.tokenizer import TokenKind, tokenize


class TestResolveParameters:
    """Test @variable resolution inside backtick content."""

    def test_resolve_datetime(self):
        result = resolve_parameters("Query at @datetime")
        assert "@datetime" not in result
        # ISO format check
        assert "T" in result
        assert ":" in result

    def test_resolve_date(self):
        result = resolve_parameters("News from @date")
        assert "@date" not in result
        assert re.match(r"News from \d{4}-\d{2}-\d{2}", result)

    def test_resolve_time(self):
        result = resolve_parameters("Current time is @time")
        assert "@time" not in result
        assert re.match(r"Current time is \d{2}:\d{2}:\d{2}", result)

    def test_resolve_user(self):
        result = resolve_parameters("User is @user", user_id="alice")
        assert result == "User is alice"

    def test_resolve_user_default(self):
        result = resolve_parameters("User is @user")
        assert result == "User is unknown"

    def test_resolve_project(self):
        result = resolve_parameters("Project is @project")
        assert result == "Project is default"

    def test_no_params(self):
        text = "Just a normal string"
        assert resolve_parameters(text) == text

    def test_multiple_params(self):
        result = resolve_parameters("At @date by @user")
        assert "@date" not in result
        assert "@user" not in result

    def test_does_not_resolve_context_sigil(self):
        """@context should NOT be resolved as a parameter."""
        result = resolve_parameters("@context('query')")
        assert "@context" in result  # unchanged


class TestResolveBacktickParams:
    """Test backtick-to-single-quote conversion with parameter resolution."""

    def test_basic_backtick_resolution(self):
        text = "@agent:name(`query at @date`)"
        result = resolve_backtick_params(text)
        # Should be converted to single quotes
        assert "`" not in result
        assert "'" in result
        assert "@date" not in result

    def test_no_backticks_unchanged(self):
        text = "@agent:name('static query')"
        assert resolve_backtick_params(text) == text

    def test_mixed_backtick_and_quoted(self):
        text = "@context('static') @agent:name(`dynamic @datetime`)"
        result = resolve_backtick_params(text)
        assert "@context('static')" in result
        assert "`" not in result
        assert "@datetime" not in result

    def test_backtick_with_user(self):
        text = "@agent:helper(`task for @user`)"
        result = resolve_backtick_params(text, user_id="bob")
        assert "bob" in result
        assert "`" not in result


class TestBacktickTokenization:
    """Test that backtick-delimited args are tokenized correctly."""

    def test_backtick_arg_tokenizes(self):
        tokens = tokenize("@agent:name(`some query`)")
        assert len(tokens) == 1
        assert tokens[0].kind == TokenKind.AGENT

    def test_backtick_workflow_tokenizes(self):
        tokens = tokenize("@wf:pipeline(`input text`)")
        assert len(tokens) == 1
        assert tokens[0].kind == TokenKind.WORKFLOW

    def test_backtick_context_tokenizes(self):
        tokens = tokenize("@context(`search query`)")
        assert len(tokens) == 1
        assert tokens[0].kind == TokenKind.CONTEXT
