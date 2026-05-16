# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for @wf:name('input') directive — tokenizer, parser, resolver."""

import pytest

from sagewai.directives.tokenizer import TokenKind, has_directives, tokenize
from sagewai.directives.parser import parse
from sagewai.directives.ast import DirectiveType, WorkflowNode


class TestWorkflowTokenizer:
    """Test the tokenizer recognizes @wf:name('input') syntax."""

    def test_tokenize_workflow_directive(self):
        tokens = tokenize("@wf:research-pipeline('latest AI trends')")
        assert len(tokens) == 1
        assert tokens[0].kind == TokenKind.WORKFLOW
        assert "@wf:research-pipeline" in tokens[0].value

    def test_tokenize_workflow_with_text(self):
        tokens = tokenize("Please run @wf:my-flow('test input') and summarize")
        assert len(tokens) == 3
        assert tokens[0].kind == TokenKind.TEXT
        assert tokens[1].kind == TokenKind.WORKFLOW
        assert tokens[2].kind == TokenKind.TEXT

    def test_has_directives_with_workflow(self):
        assert has_directives("@wf:name('input')")

    def test_tokenize_workflow_double_quotes(self):
        tokens = tokenize('@wf:pipeline("some input")')
        assert len(tokens) == 1
        assert tokens[0].kind == TokenKind.WORKFLOW

    def test_workflow_mixed_with_agent(self):
        text = "@agent:scout('find info') then @wf:pipeline('process it')"
        tokens = tokenize(text)
        kinds = [t.kind for t in tokens]
        assert TokenKind.AGENT in kinds
        assert TokenKind.WORKFLOW in kinds


class TestWorkflowParser:
    """Test the parser creates WorkflowNode from @wf tokens."""

    def test_parse_workflow_directive(self):
        nodes = parse("@wf:research-pipeline('latest AI trends')")
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, WorkflowNode)
        assert node.workflow_name == "research-pipeline"
        assert node.input_text == "latest AI trends"
        assert node.node_type == DirectiveType.WORKFLOW

    def test_parse_workflow_with_dots(self):
        nodes = parse("@wf:my.flow('test')")
        assert len(nodes) == 1
        assert isinstance(nodes[0], WorkflowNode)
        assert nodes[0].workflow_name == "my.flow"

    def test_parse_mixed_workflow_and_context(self):
        nodes = parse("@context('search query') @wf:pipeline('input')")
        assert len(nodes) >= 2
        types = [n.node_type for n in nodes]
        assert DirectiveType.CONTEXT in types
        assert DirectiveType.WORKFLOW in types


@pytest.mark.asyncio
class TestWorkflowResolver:
    """Test the resolver handles WorkflowNode resolution."""

    async def test_resolve_workflow_no_store(self):
        from sagewai.directives.resolver import DirectiveResolver

        resolver = DirectiveResolver()  # no workflows injected
        node = WorkflowNode(workflow_name="test", input_text="hello")
        result = await resolver._resolve_one(node)
        assert result.error is not None
        assert "No workflow store configured" in result.error

    async def test_resolve_workflow_not_found(self):
        from sagewai.directives.resolver import DirectiveResolver
        from sagewai.admin.workflow_store import InMemorySavedWorkflowStore

        store = InMemorySavedWorkflowStore()
        resolver = DirectiveResolver(workflows=store)
        node = WorkflowNode(workflow_name="nonexistent", input_text="hello")
        result = await resolver._resolve_one(node)
        assert result.error is not None
        assert "not found" in result.error.lower()
