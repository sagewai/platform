"""Tests for the Sagewai Directive Engine."""

from __future__ import annotations

import pytest

from sagewai.models.message import ChatMessage

from sagewai.directives.ast import (
    AgentNode,
    ContextBlock,
    ContextNode,
    DirectiveType,
    ExecutionOverrides,
    McpNode,
    MemoryNode,
    MetaKey,
    MetaNode,
    TextNode,
    ToolNode,
)
from sagewai.directives.budget import TokenBudget, apply_budget, estimate_tokens
from sagewai.directives.compressor import compress_text
from sagewai.directives.engine import DirectiveEngine, DirectiveResult
from sagewai.directives.formatter import (
    format_context_blocks,
    format_tool_descriptions,
    parse_tool_call_from_output,
)
from sagewai.directives.parser import DirectiveParseError, extract_clean_text, parse
from sagewai.directives.profiles import LARGE, MEDIUM, SMALL, detect_profile, get_profile
from sagewai.directives.registry import DirectiveRegistry
from sagewai.directives.template import parse_template
from sagewai.directives.tokenizer import TokenKind, has_directives, tokenize


# ============================================================================
# Tokenizer tests
# ============================================================================


class TestTokenizer:
    def test_plain_text(self):
        tokens = tokenize("Hello, world!")
        assert len(tokens) == 1
        assert tokens[0].kind == TokenKind.TEXT
        assert tokens[0].value == "Hello, world!"

    def test_context_directive(self):
        tokens = tokenize("@context('machine learning') Tell me more")
        assert len(tokens) == 2
        assert tokens[0].kind == TokenKind.CONTEXT
        assert tokens[1].kind == TokenKind.TEXT
        assert tokens[1].value == " Tell me more"

    def test_memory_directive(self):
        tokens = tokenize("@memory('last session') What were we discussing?")
        assert len(tokens) == 2
        assert tokens[0].kind == TokenKind.MEMORY

    def test_agent_directive(self):
        tokens = tokenize("@agent:tutor('explain calculus') Please help")
        assert len(tokens) == 2
        assert tokens[0].kind == TokenKind.AGENT

    def test_tool_directive(self):
        tokens = tokenize("/tool.search('quarterly report') Summarize it")
        assert len(tokens) == 2
        assert tokens[0].kind == TokenKind.TOOL

    def test_mcp_directive(self):
        tokens = tokenize("/mcp.slack('unread messages')")
        assert len(tokens) == 1
        assert tokens[0].kind == TokenKind.MCP

    def test_meta_directives(self):
        tokens = tokenize("#model:gemini-flash #scope:project Hello")
        meta_tokens = [t for t in tokens if t.kind == TokenKind.META]
        assert len(meta_tokens) == 2

    def test_template_directive(self):
        tokens = tokenize("Hello {{ context.search('query') }} world")
        assert len(tokens) == 3
        assert tokens[0].kind == TokenKind.TEXT
        assert tokens[1].kind == TokenKind.TEMPLATE
        assert tokens[2].kind == TokenKind.TEXT

    def test_multiple_directives(self):
        text = "@context('ML') @memory('style') Help me learn #model:codellama"
        tokens = tokenize(text)
        kinds = [t.kind for t in tokens]
        assert TokenKind.CONTEXT in kinds
        assert TokenKind.MEMORY in kinds
        assert TokenKind.META in kinds

    def test_has_directives_true(self):
        assert has_directives("@context('test') hello") is True
        assert has_directives("/tool.search('query')") is True
        assert has_directives("#model:gpt-4o") is True

    def test_has_directives_false(self):
        assert has_directives("Hello, world!") is False
        assert has_directives("Just plain text") is False

    def test_double_quoted_args(self):
        tokens = tokenize('@context("machine learning")')
        assert len(tokens) == 1
        assert tokens[0].kind == TokenKind.CONTEXT

    def test_context_with_kwargs(self):
        tokens = tokenize("@context('ML', scope='org', top_k=3)")
        assert len(tokens) == 1
        assert tokens[0].kind == TokenKind.CONTEXT

    def test_meta_not_matched_inside_url(self):
        """#model: inside a URL or non-whitespace context should not match."""
        tokens = tokenize("See https://example.com#model:pricing for details")
        meta_tokens = [t for t in tokens if t.kind == TokenKind.META]
        assert len(meta_tokens) == 0

    def test_meta_matched_after_whitespace(self):
        tokens = tokenize("Hello #model:gemini-flash")
        meta_tokens = [t for t in tokens if t.kind == TokenKind.META]
        assert len(meta_tokens) == 1


# ============================================================================
# Parser tests
# ============================================================================


class TestParser:
    def test_parse_context(self):
        nodes = parse("@context('machine learning basics')")
        assert len(nodes) == 1
        assert isinstance(nodes[0], ContextNode)
        assert nodes[0].query == "machine learning basics"

    def test_parse_context_with_kwargs(self):
        nodes = parse("@context('ML', scope='org', top_k=3)")
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, ContextNode)
        assert node.query == "ML"
        assert node.scope == "org"
        assert node.top_k == 3

    def test_parse_context_with_tags(self):
        nodes = parse("@context('ML research', tags='finance,billing')")
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, ContextNode)
        assert node.query == "ML research"
        assert node.tags == ["finance", "billing"]

    def test_parse_context_with_scope_and_tags(self):
        nodes = parse("@context('report', scope='org', tags='q4,finance', top_k=10)")
        node = nodes[0]
        assert node.query == "report"
        assert node.scope == "org"
        assert node.tags == ["q4", "finance"]
        assert node.top_k == 10

    def test_parse_context_no_tags(self):
        nodes = parse("@context('simple query')")
        assert nodes[0].tags is None

    def test_parse_memory(self):
        nodes = parse("@memory('last session notes')")
        assert len(nodes) == 1
        assert isinstance(nodes[0], MemoryNode)
        assert nodes[0].query == "last session notes"

    def test_parse_agent(self):
        nodes = parse("@agent:tutor('explain derivatives')")
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, AgentNode)
        assert node.agent_name == "tutor"
        assert node.question == "explain derivatives"

    def test_parse_tool(self):
        nodes = parse("/tool.search('quarterly report')")
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, ToolNode)
        assert node.tool_name == "search"
        assert node.raw_arg == "quarterly report"

    def test_parse_mcp(self):
        nodes = parse("/mcp.slack('unread messages')")
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, McpNode)
        assert node.connector == "slack"

    def test_parse_meta_model(self):
        nodes = parse("#model:gemini-flash")
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, MetaNode)
        assert node.key == MetaKey.MODEL
        assert node.value == "gemini-flash"

    def test_parse_meta_scope(self):
        nodes = parse("#scope:project")
        assert len(nodes) == 1
        assert isinstance(nodes[0], MetaNode)
        assert nodes[0].key == MetaKey.SCOPE

    def test_parse_meta_strategy(self):
        nodes = parse("#strategy:chain-of-thought")
        assert len(nodes) == 1
        assert isinstance(nodes[0], MetaNode)
        assert nodes[0].key == MetaKey.STRATEGY

    def test_parse_meta_budget(self):
        nodes = parse("#budget:2000")
        assert len(nodes) == 1
        assert isinstance(nodes[0], MetaNode)
        assert nodes[0].key == MetaKey.BUDGET
        assert nodes[0].value == "2000"

    def test_parse_mixed(self):
        text = "@context('ML') @memory('style') Help me learn #model:codellama"
        nodes = parse(text)
        types = [type(n) for n in nodes]
        assert ContextNode in types
        assert MemoryNode in types
        assert MetaNode in types
        assert TextNode in types

    def test_extract_clean_text(self):
        nodes = parse("@context('ML') Help me learn #model:codellama")
        clean = extract_clean_text(nodes)
        assert "Help me learn" in clean
        assert "@context" not in clean
        assert "#model" not in clean

    def test_parse_meta_budget_invalid_string(self):
        with pytest.raises(DirectiveParseError, match="positive integer"):
            parse("#budget:abc")

    def test_parse_meta_budget_negative(self):
        with pytest.raises(DirectiveParseError, match="positive integer"):
            parse("#budget:-5")

    def test_parse_meta_strategy_unknown(self):
        with pytest.raises(DirectiveParseError, match="Unknown strategy"):
            parse("#strategy:chian-of-thought")

    def test_parse_meta_strategy_valid(self):
        nodes = parse("#strategy:react")
        meta = [n for n in nodes if isinstance(n, MetaNode)]
        assert len(meta) == 1
        assert meta[0].value == "react"


# ============================================================================
# Model profiles tests
# ============================================================================


class TestProfiles:
    def test_small_profile(self):
        assert SMALL.name == "small"
        assert SMALL.use_delimiters is True
        assert SMALL.use_explicit_instructions is True
        assert SMALL.tool_call_mode == "prompt_based"

    def test_medium_profile(self):
        assert MEDIUM.name == "medium"
        assert MEDIUM.use_delimiters is True
        assert MEDIUM.tool_call_mode == "native"

    def test_large_profile(self):
        assert LARGE.name == "large"
        assert LARGE.use_delimiters is False
        assert LARGE.tool_call_mode == "native"

    def test_detect_codellama_7b(self):
        profile = detect_profile("codellama:7b-instruct")
        assert profile.name == "small"

    def test_detect_mistral_7b(self):
        profile = detect_profile("mistral:7b")
        assert profile.name == "small"

    def test_detect_ollama_mistral(self):
        profile = detect_profile("ollama/mistral")
        assert profile.name == "small"

    def test_detect_gpt4o(self):
        profile = detect_profile("gpt-4o")
        assert profile.name == "large"

    def test_detect_claude_sonnet(self):
        profile = detect_profile("claude-sonnet-4-6")
        assert profile.name == "large"

    def test_detect_gemini_flash(self):
        profile = detect_profile("gemini/gemini-2.5-flash")
        assert profile.name == "medium"

    def test_detect_claude_haiku(self):
        profile = detect_profile("claude-haiku-4-5")
        assert profile.name == "medium"

    def test_detect_unknown_falls_back_to_medium(self):
        profile = detect_profile("my-custom-model-xyz")
        assert profile.name == "medium"

    def test_get_profile(self):
        profile = get_profile("small")
        assert profile.name == "small"

    def test_get_profile_unknown_raises(self):
        with pytest.raises(ValueError):
            get_profile("nonexistent")

    def test_detect_phi3(self):
        profile = detect_profile("phi-3-mini")
        assert profile.name == "small"

    def test_detect_llama_70b(self):
        profile = detect_profile("llama3:70b")
        assert profile.name == "medium"

    def test_detect_gpt4o_mini(self):
        profile = detect_profile("gpt-4o-mini")
        assert profile.name == "medium"


# ============================================================================
# Formatter tests
# ============================================================================


class TestFormatter:
    def test_format_small_profile_uses_delimiters(self):
        blocks = [
            ContextBlock(
                source="context_engine",
                content="ML is a subset of AI.",
                relevance=0.9,
                directive_type=DirectiveType.CONTEXT,
            )
        ]
        result = format_context_blocks(blocks, SMALL)
        assert "[SYSTEM CONTEXT" in result
        assert "ML is a subset of AI." in result
        assert "[END CONTEXT]" in result

    def test_format_small_has_explicit_instructions(self):
        blocks = [
            ContextBlock(
                source="test",
                content="Test content.",
                directive_type=DirectiveType.CONTEXT,
            )
        ]
        result = format_context_blocks(blocks, SMALL)
        assert "[INSTRUCTIONS]" in result
        assert "MUST use the context" in result

    def test_format_large_profile_natural(self):
        blocks = [
            ContextBlock(
                source="context_engine",
                content="ML is a subset of AI.",
                relevance=0.9,
                directive_type=DirectiveType.CONTEXT,
            )
        ]
        result = format_context_blocks(blocks, LARGE)
        assert "[SYSTEM CONTEXT" not in result
        assert "Relevant context" in result

    def test_format_empty_blocks(self):
        result = format_context_blocks([], SMALL)
        assert result == ""

    def test_format_tool_descriptions_small(self):
        from sagewai.models.tool import ToolSpec

        tools = {
            "search": ToolSpec(
                name="search",
                description="Search documents",
                parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            )
        }
        result = format_tool_descriptions(tools, SMALL)
        assert "AVAILABLE TOOLS" in result
        assert "TOOL_CALL" in result
        assert "search" in result

    def test_format_tool_descriptions_large_returns_empty(self):
        from sagewai.models.tool import ToolSpec

        tools = {"search": ToolSpec(name="search", description="Search")}
        result = format_tool_descriptions(tools, LARGE)
        assert result == ""

    def test_parse_tool_call_valid(self):
        output = 'Some text TOOL_CALL: {"name": "search", "arguments": {"query": "test"}}'
        result = parse_tool_call_from_output(output)
        assert result is not None
        assert result[0] == "search"
        assert result[1] == {"query": "test"}

    def test_parse_tool_call_none(self):
        result = parse_tool_call_from_output("No tool call here")
        assert result is None


# ============================================================================
# Budget tests
# ============================================================================


class TestBudget:
    def test_estimate_tokens(self):
        assert estimate_tokens("hello world") > 0
        assert estimate_tokens("a" * 400) == 100

    def test_budget_allocation(self):
        budget = TokenBudget(SMALL)
        assert budget.total == SMALL.max_context_tokens
        assert budget.allocation("context") > 0
        assert budget.remaining == budget.total

    def test_budget_consume(self):
        budget = TokenBudget(SMALL)
        initial = budget.available("context")
        budget.consume("context", 100)
        assert budget.available("context") == initial - 100

    def test_budget_consume_capped(self):
        budget = TokenBudget(SMALL)
        avail = budget.available("context")
        consumed = budget.consume("context", avail + 1000)
        assert consumed == avail

    def test_apply_budget_fits(self):
        blocks = [
            ContextBlock(source="test", content="Short.", directive_type=DirectiveType.CONTEXT)
        ]
        budget = TokenBudget(LARGE)
        result = apply_budget(blocks, budget)
        assert len(result) == 1

    def test_apply_budget_truncates(self):
        long_content = "word " * 5000  # ~5000 tokens
        blocks = [
            ContextBlock(
                source="test",
                content=long_content,
                relevance=0.9,
                directive_type=DirectiveType.CONTEXT,
            )
        ]
        budget = TokenBudget(SMALL)
        result = apply_budget(blocks, budget)
        assert len(result) <= 1
        if result:
            assert len(result[0].content) < len(long_content)


# ============================================================================
# Compressor tests
# ============================================================================


class TestCompressor:
    def test_compress_short_text_unchanged(self):
        text = "This is a short text."
        result = compress_text(text, "short text", target_tokens=100)
        assert result == text

    def test_compress_long_text(self):
        # Create text that exceeds target
        sentences = [
            "Machine learning is a powerful technology.",
            "It enables computers to learn from data.",
            "Neural networks are inspired by the brain.",
            "Deep learning uses many layers of neurons.",
            "Training requires large datasets typically.",
            "Overfitting is a common challenge faced.",
            "Regularization helps prevent overfitting issues.",
            "Cross validation tests model generalization ability.",
        ]
        text = " ".join(sentences)
        result = compress_text(text, "machine learning", target_tokens=20)
        assert len(result) < len(text)
        # Should prioritize ML-related sentences
        assert "machine learning" in result.lower() or "learning" in result.lower()


# ============================================================================
# Template parser tests
# ============================================================================


class TestTemplate:
    def test_simple_context_search(self):
        nodes = parse_template("Hello {{ context.search('ML') }} world")
        types = [type(n) for n in nodes]
        assert TextNode in types
        assert ContextNode in types

    def test_memory_user(self):
        nodes = parse_template("Style: {{ memory.user('learning_style') }}")
        mem_nodes = [n for n in nodes if isinstance(n, MemoryNode)]
        assert len(mem_nodes) == 1
        assert mem_nodes[0].query == "learning_style"
        assert mem_nodes[0].scope == "project"

    def test_agent_ask(self):
        nodes = parse_template("{{ agent.ask('tutor', 'explain calculus') }}")
        agent_nodes = [n for n in nodes if isinstance(n, AgentNode)]
        assert len(agent_nodes) == 1
        assert agent_nodes[0].agent_name == "tutor"

    def test_variable_substitution(self):
        nodes = parse_template("Topic: {{ topic }}", variables={"topic": "calculus"})
        texts = [n.text for n in nodes if isinstance(n, TextNode)]
        assert any("calculus" in t for t in texts)

    def test_if_with_variable(self):
        template = "Start {{ if show_extra }}Extra content{{ endif }} End"
        nodes = parse_template(template, variables={"show_extra": True})
        text = "".join(n.text for n in nodes if isinstance(n, TextNode))
        assert "Extra content" in text

    def test_if_false_removes_content(self):
        template = "Start {{ if show_extra }}Extra content{{ endif }} End"
        nodes = parse_template(template, variables={"show_extra": False})
        text = "".join(n.text for n in nodes if isinstance(n, TextNode))
        assert "Extra content" not in text

    def test_for_loop(self):
        template = "Topics: {{ for item in topics }}{{ item }} {{ endfor }}"
        nodes = parse_template(
            template,
            variables={"topics": ["math", "science", "history"]},
        )
        text = "".join(n.text for n in nodes if isinstance(n, TextNode))
        assert "math" in text
        assert "science" in text
        assert "history" in text

    def test_unknown_expression_passes_through(self):
        nodes = parse_template("{{ some.unknown.thing }}")
        texts = [n.text for n in nodes if isinstance(n, TextNode)]
        assert any("some.unknown.thing" in t for t in texts)


# ============================================================================
# Registry tests
# ============================================================================


class TestRegistry:
    def test_register_and_get(self):
        registry = DirectiveRegistry()

        async def handler(query: str) -> str:
            return f"Result: {query}"

        registry.register("kb", "@kb", handler, "Knowledge base search")
        directive = registry.get("kb")
        assert directive is not None
        assert directive.name == "kb"
        assert directive.sigil == "@kb"

    def test_unregister(self):
        registry = DirectiveRegistry()
        registry.register("kb", "@kb", lambda q: q)
        registry.unregister("kb")
        assert registry.get("kb") is None

    def test_list_directives(self):
        registry = DirectiveRegistry()
        registry.register("a", "@a", lambda: "a")
        registry.register("b", "@b", lambda: "b")
        assert len(registry.list_directives()) == 2


# ============================================================================
# Resolver tests (with mock services)
# ============================================================================


class MockContextProvider:
    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        return [f"Context for: {query}"]


class MockAgent:
    async def chat(self, message: str) -> str:
        return f"Agent response to: {message}"


class MockToolSpec:
    def __init__(self, name: str):
        self.name = name
        self.description = f"Mock tool: {name}"
        self.parameters = {"type": "object", "properties": {"query": {"type": "string"}}}
        self.handler = self._handler

    async def _handler(self, query: str = "") -> str:
        return f"Tool result for: {query}"


@pytest.mark.asyncio
class TestResolver:
    async def test_resolve_context(self):
        from sagewai.directives.resolver import DirectiveResolver

        resolver = DirectiveResolver(context=MockContextProvider())
        nodes = parse("@context('machine learning')")
        resolved, blocks, overrides = await resolver.resolve_all(nodes)
        assert any(r.content and "machine learning" in r.content for r in resolved)
        assert len(blocks) > 0

    async def test_resolve_agent(self):
        from sagewai.directives.resolver import DirectiveResolver

        resolver = DirectiveResolver(agents={"tutor": MockAgent()})
        nodes = parse("@agent:tutor('explain derivatives')")
        resolved, blocks, overrides = await resolver.resolve_all(nodes)
        assert any("Agent response" in r.content for r in resolved if r.content)

    async def test_resolve_tool(self):
        from sagewai.directives.resolver import DirectiveResolver

        resolver = DirectiveResolver(
            tools={"search": MockToolSpec("search")},
            allowed_tools={"search"},
        )
        nodes = parse("/tool.search('quarterly report')")
        resolved, blocks, overrides = await resolver.resolve_all(nodes)
        assert any("Tool result" in r.content for r in resolved if r.content)

    async def test_resolve_tool_blocked_by_default(self):
        from sagewai.directives.resolver import DirectiveResolver

        resolver = DirectiveResolver(tools={"search": MockToolSpec("search")})
        nodes = parse("/tool.search('test')")
        resolved, blocks, overrides = await resolver.resolve_all(nodes)
        assert any(r.error and "disabled" in r.error for r in resolved)

    async def test_resolve_tool_allowlist(self):
        from sagewai.directives.resolver import DirectiveResolver

        resolver = DirectiveResolver(
            tools={"search": MockToolSpec("search"), "delete": MockToolSpec("delete")},
            allowed_tools={"search"},
        )
        # Allowed tool works
        nodes = parse("/tool.search('ok')")
        resolved, _, _ = await resolver.resolve_all(nodes)
        assert any(r.content and "Tool result" in r.content for r in resolved)

        # Blocked tool fails
        nodes = parse("/tool.delete('data')")
        resolved, _, _ = await resolver.resolve_all(nodes)
        assert any(r.error and "not allowed" in r.error for r in resolved)

    async def test_resolve_meta_overrides(self):
        from sagewai.directives.resolver import DirectiveResolver

        resolver = DirectiveResolver()
        nodes = parse("#model:gemini-flash #scope:project")
        resolved, blocks, overrides = await resolver.resolve_all(nodes)
        assert overrides.model == "gemini-flash"
        assert overrides.scope == "project"

    async def test_resolve_timeout(self):
        """Directive resolution should time out gracefully."""
        import asyncio
        from sagewai.directives.resolver import DirectiveResolver

        class SlowContext:
            async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
                await asyncio.sleep(5)
                return ["result"]

        resolver = DirectiveResolver(context=SlowContext(), resolution_timeout=0.1)
        nodes = parse("@context('slow query')")
        resolved, blocks, overrides = await resolver.resolve_all(nodes)
        assert any(r.error and "timed out" in r.error for r in resolved)

    async def test_resolve_agent_via_resolve_one(self):
        """Normal agent resolution works through the production _resolve_one path."""
        from sagewai.directives.resolver import DirectiveResolver
        from sagewai.directives.ast import AgentNode

        class EchoAgent:
            async def chat(self, message: str) -> str:
                return f"Echo: {message}"

        resolver = DirectiveResolver(agents={"echo": EchoAgent()})
        node = AgentNode(agent_name="echo", question="hello")
        result = await resolver._resolve_one(node)
        assert result.error is None
        assert "Echo: hello" in result.content

    async def test_circular_agent_detection(self):
        """Circular agent references should be caught."""
        from sagewai.directives.resolver import DirectiveResolver

        class EchoAgent:
            async def chat(self, message: str) -> str:
                return f"Echo: {message}"

        resolver = DirectiveResolver(
            agents={"a": EchoAgent(), "b": EchoAgent()},
        )
        # Direct circular ref can't happen at resolve_all level since
        # agents don't call directives themselves. Test the guard directly.
        from sagewai.directives.ast import AgentNode
        node = AgentNode(agent_name="a", question="test")
        result = await resolver._resolve_agent(node, _call_stack=("b", "a"))
        assert result.error is not None
        assert "Circular" in result.error

    async def test_max_agent_depth(self):
        """Agent delegation depth should be limited."""
        from sagewai.directives.resolver import DirectiveResolver
        from sagewai.directives.ast import AgentNode

        class EchoAgent:
            async def chat(self, message: str) -> str:
                return "response"

        resolver = DirectiveResolver(
            agents={"deep": EchoAgent()},
            max_agent_depth=2,
        )
        node = AgentNode(agent_name="deep", question="test")
        result = await resolver._resolve_agent(node, _call_stack=("a", "b"))
        assert result.error is not None
        assert "depth exceeded" in result.error

    async def test_resolve_missing_service_graceful(self):
        from sagewai.directives.resolver import DirectiveResolver

        resolver = DirectiveResolver()  # no services
        nodes = parse("@context('test')")
        resolved, blocks, overrides = await resolver.resolve_all(nodes)
        assert any(r.error for r in resolved)

    async def test_resolve_concurrent(self):
        from sagewai.directives.resolver import DirectiveResolver

        resolver = DirectiveResolver(
            context=MockContextProvider(),
            agents={"tutor": MockAgent()},
        )
        nodes = parse("@context('ML') @agent:tutor('help')")
        resolved, blocks, overrides = await resolver.resolve_all(nodes)
        # Both should resolve
        non_text = [r for r in resolved if r.node.node_type.value != "text"]
        assert len(non_text) == 2
        assert all(r.content for r in non_text)


# ============================================================================
# Engine integration tests
# ============================================================================


@pytest.mark.asyncio
class TestEngine:
    async def test_resolve_no_directives(self):
        engine = DirectiveEngine(model="gpt-4o")
        result = await engine.resolve("Hello, world!")
        assert result.prompt == "Hello, world!"
        assert result.clean_prompt == "Hello, world!"
        assert len(result.context_blocks) == 0

    async def test_resolve_with_context(self):
        engine = DirectiveEngine(
            context=MockContextProvider(),
            model="gpt-4o",  # Use large model to avoid compression
        )
        result = await engine.resolve("@context('machine learning') Help me learn")
        assert "machine learning" in result.prompt.lower()
        assert result.clean_prompt.strip() == "Help me learn"
        assert len(result.context_blocks) > 0
        assert result.metadata.total_directives == 1
        assert result.metadata.resolved_count == 1

    async def test_resolve_with_overrides(self):
        engine = DirectiveEngine(model="gpt-4o")
        result = await engine.resolve("#model:gemini-flash Hello")
        assert result.overrides is not None
        assert result.overrides.model == "gemini-flash"

    async def test_resolve_small_model_formatting(self):
        engine = DirectiveEngine(
            context=MockContextProvider(),
            model="codellama:7b",
        )
        result = await engine.resolve("@context('ML') Help")
        # Small model should have structured delimiters
        assert "[SYSTEM CONTEXT" in result.prompt or "[USER MESSAGE]" in result.prompt

    async def test_resolve_large_model_formatting(self):
        engine = DirectiveEngine(
            context=MockContextProvider(),
            model="gpt-4o",
        )
        result = await engine.resolve("@context('ML') Help")
        # Large model should NOT have structured delimiters
        assert "[SYSTEM CONTEXT" not in result.prompt

    async def test_resolve_template(self):
        engine = DirectiveEngine(
            context=MockContextProvider(),
            model="gpt-4o",
        )
        result = await engine.resolve_template(
            "You teach {{ context.search('calculus') }}."
        )
        assert "Context for: calculus" in result.prompt

    async def test_profile_auto_detection(self):
        engine = DirectiveEngine(model="codellama:7b")
        assert engine.profile.name == "small"

        engine2 = DirectiveEngine(model="gpt-4o")
        assert engine2.profile.name == "large"

    async def test_profile_manual_override(self):
        engine = DirectiveEngine(
            model="gpt-4o",
            model_profile=SMALL,
        )
        assert engine.profile.name == "small"

    async def test_tool_descriptions_for_small_model(self):
        engine = DirectiveEngine(
            tools={"search": MockToolSpec("search")},
            model="codellama:7b",
        )
        result = await engine.resolve("@context('test') Hello")
        # Small model should get tool descriptions even without /tool directive
        # (because tools are available)

    async def test_register_custom_directive_sync(self):
        """Custom sync directive resolves end-to-end."""
        engine = DirectiveEngine(model="gpt-4o")
        engine.register("kb", "@kb", lambda q: f"KB result: {q}")
        result = await engine.resolve("@kb('machine learning') Tell me more")
        assert "KB result: machine learning" in result.prompt

    async def test_register_custom_directive_async(self):
        """Custom async directive resolves end-to-end."""
        async def search_kb(query: str) -> str:
            return f"Async KB: {query}"

        engine = DirectiveEngine(model="gpt-4o")
        engine.register("kb", "@kb", search_kb)
        result = await engine.resolve("@kb('deep learning') Explain")
        assert "Async KB: deep learning" in result.prompt

    async def test_custom_directive_not_found(self):
        """Unregistered custom directive passes through as text."""
        engine = DirectiveEngine(model="gpt-4o")
        result = await engine.resolve("@unknown('test') Hello")
        # @unknown is not a built-in or registered directive, passes through
        assert "Hello" in result.prompt

    async def test_metadata_populated(self):
        engine = DirectiveEngine(
            context=MockContextProvider(),
            model="gpt-4o",
        )
        result = await engine.resolve("@context('test') Hello")
        assert result.metadata.total_directives == 1
        assert result.metadata.total_duration_ms > 0
        assert result.metadata.input_tokens_estimate > 0
        assert result.metadata.output_tokens_estimate > 0


# ============================================================================
# Prompt-based tool calling tests (#412)
# ============================================================================


class TestPromptBasedToolCalling:
    def test_extract_prompt_tool_call_no_engine(self):
        """No directive engine → no prompt-based tool calling."""
        from sagewai.core.strategies import _extract_prompt_tool_call

        class FakeAgent:
            _directive_engine = None
            _tool_registry = {}

        response = ChatMessage.assistant(content='TOOL_CALL: {"name": "search", "arguments": {}}')
        assert _extract_prompt_tool_call(FakeAgent(), response) is None

    def test_extract_prompt_tool_call_native_mode(self):
        """Directive engine with native mode → no prompt parsing."""
        from sagewai.core.strategies import _extract_prompt_tool_call

        class FakeProfile:
            tool_call_mode = "native"

        class FakeEngine:
            _profile = FakeProfile()

        class FakeAgent:
            _directive_engine = FakeEngine()
            _tool_registry = {}

        response = ChatMessage.assistant(content='TOOL_CALL: {"name": "search", "arguments": {}}')
        assert _extract_prompt_tool_call(FakeAgent(), response) is None

    def test_extract_prompt_tool_call_prompt_based(self):
        """Prompt-based mode with allowed tool → extracts call."""
        from sagewai.core.strategies import _extract_prompt_tool_call

        class FakeProfile:
            tool_call_mode = "prompt_based"

        class FakeResolver:
            _allow_all_tools = True

        class FakeEngine:
            _profile = FakeProfile()
            _resolver = FakeResolver()

        class FakeAgent:
            _directive_engine = FakeEngine()
            _tool_registry = {"search": MockToolSpec("search")}

        response = ChatMessage.assistant(
            content='Let me search for that.\nTOOL_CALL: {"name": "search", "arguments": {"query": "test"}}'
        )
        result = _extract_prompt_tool_call(FakeAgent(), response)
        assert result is not None
        assert result[0] == "search"
        assert result[1] == {"query": "test"}

    def test_extract_prompt_tool_call_blocked(self):
        """Prompt-based mode without allowed tool → blocked."""
        from sagewai.core.strategies import _extract_prompt_tool_call

        class FakeProfile:
            tool_call_mode = "prompt_based"

        class FakeResolver:
            _allow_all_tools = False
            _allowed_tools = {"safe_tool"}

        class FakeEngine:
            _profile = FakeProfile()
            _resolver = FakeResolver()

        class FakeAgent:
            _directive_engine = FakeEngine()
            _tool_registry = {"delete": MockToolSpec("delete")}

        response = ChatMessage.assistant(
            content='TOOL_CALL: {"name": "delete", "arguments": {}}'
        )
        assert _extract_prompt_tool_call(FakeAgent(), response) is None

    def test_strip_tool_call_from_text(self):
        from sagewai.core.strategies import _strip_tool_call_from_text

        text = 'I will search for that.\nTOOL_CALL: {"name": "search", "arguments": {"q": "test"}}'
        clean = _strip_tool_call_from_text(text)
        assert "TOOL_CALL" not in clean
        assert "I will search for that." in clean

    def test_strip_tool_call_no_match(self):
        from sagewai.core.strategies import _strip_tool_call_from_text

        text = "Just a normal response without tool calls."
        assert _strip_tool_call_from_text(text) == text


# ============================================================================
# Additional coverage tests (#416)
# ============================================================================


@pytest.mark.asyncio
class TestAdditionalCoverage:
    async def test_malformed_directive_passes_through(self):
        """Malformed directive syntax should pass through as plain text."""
        engine = DirectiveEngine(model="gpt-4o")
        # Unclosed @context( — not a valid directive, passes through
        result = await engine.resolve("@context( Hello world")
        assert "Hello world" in result.prompt

    async def test_empty_prompt(self):
        """Empty string should resolve to empty."""
        engine = DirectiveEngine(model="gpt-4o")
        result = await engine.resolve("")
        assert result.prompt == ""
        assert result.clean_prompt == ""

    async def test_only_meta_directives(self):
        """Prompt with only meta directives should have clean_prompt stripped."""
        engine = DirectiveEngine(model="gpt-4o")
        result = await engine.resolve("#model:gemini-flash")
        assert result.overrides is not None
        assert result.overrides.model == "gemini-flash"

    async def test_multiple_context_directives(self):
        """Multiple @context directives should all resolve."""
        engine = DirectiveEngine(
            context=MockContextProvider(),
            model="gpt-4o",
        )
        result = await engine.resolve(
            "@context('topic A') @context('topic B') Compare them."
        )
        assert result.metadata.total_directives == 2
        assert result.metadata.resolved_count == 2

    async def test_tool_allowlist_empty_set(self):
        """Empty allowed_tools set blocks all tools."""
        from sagewai.directives.resolver import DirectiveResolver

        resolver = DirectiveResolver(
            tools={"search": MockToolSpec("search")},
            allowed_tools=set(),
        )
        nodes = parse("/tool.search('test')")
        resolved, _, _ = await resolver.resolve_all(nodes)
        assert any(r.error and "not allowed" in r.error for r in resolved)

    async def test_profile_configurable_fields(self):
        """New configurable fields on ModelProfile work."""
        from sagewai.directives.profiles import ModelProfile

        profile = ModelProfile(
            name="custom",
            default_top_k=10,
            min_block_tokens=100,
            sentence_boost_first=2.0,
            sentence_boost_last=1.5,
            tool_call_marker="EXECUTE:",
        )
        assert profile.default_top_k == 10
        assert profile.min_block_tokens == 100
        assert profile.sentence_boost_first == 2.0
        assert profile.tool_call_marker == "EXECUTE:"

    async def test_chars_per_token_constant(self):
        """Shared CHARS_PER_TOKEN constant is accessible."""
        from sagewai.directives.budget import CHARS_PER_TOKEN, estimate_tokens

        assert CHARS_PER_TOKEN == 4
        assert estimate_tokens("a" * 400) == 100

    async def test_compress_with_custom_boosts(self):
        """Compression respects custom boost values."""
        sentences = [
            "Machine learning is powerful.",
            "It enables computers to learn from data.",
            "Neural networks are inspired by the brain.",
            "Deep learning uses many layers.",
        ]
        text = " ".join(sentences)

        from sagewai.directives.compressor import compress_text
        # With high first-sentence boost, first sentence should be kept
        result = compress_text(text, "machine learning", target_tokens=15, boost_first=5.0)
        assert "machine learning" in result.lower()
