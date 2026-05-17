# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the @transform directive adapter and the transform tool adapter."""

from types import SimpleNamespace

import pytest

from sagewai.directives.engine import DirectiveEngine
from sagewai.transform.directive_adapter import register_transform_directive
from sagewai.transform.engine import TransformEngine
from sagewai.transform.models import TransformResult
from sagewai.transform.operations import graphify, summarize
from sagewai.transform.registry import TransformRegistry
from sagewai.transform.tool_adapter import transform_tool_spec


def _engine_with(op_name, op):
    """A TransformEngine whose registry holds a single named op."""
    reg = TransformRegistry()
    reg.register(op_name, op)
    return TransformEngine(reg)


# --------------------------------------------------------------------------
# Directive adapter (Task A7)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_directive_injects_output():
    async def _fake(content, *, project_id=None, **params):
        return TransformResult(
            operation="summarize", output=f"SUMMARY[{content}]", ok=True
        )

    engine = DirectiveEngine(model="gpt-4o")
    register_transform_directive(
        engine, transform_engine=_engine_with("summarize", _fake)
    )
    result = await engine.resolve('@transform(summarize, "some text") Tell me')
    assert "SUMMARY[some text]" in result.prompt
    assert "Tell me" in result.prompt


@pytest.mark.asyncio
async def test_transform_directive_passes_params():
    captured: dict = {}

    async def _fake(content, *, project_id=None, **params):
        captured.update(params)
        return TransformResult(operation="summarize", output="ok", ok=True)

    engine = DirectiveEngine(model="gpt-4o")
    register_transform_directive(
        engine, transform_engine=_engine_with("summarize", _fake)
    )
    await engine.resolve('@transform(summarize, "txt", max_words=42) q')
    assert captured == {"max_words": 42}


@pytest.mark.asyncio
async def test_transform_directive_failure_injects_nothing():
    # Empty engine — "summarize" is unknown → the transform fails.
    engine = DirectiveEngine(model="gpt-4o")
    register_transform_directive(
        engine, transform_engine=TransformEngine(TransformRegistry())
    )
    result = await engine.resolve('@transform(summarize, "x") Hello there')
    # Prompt resolution still succeeds; the failed transform injects nothing.
    assert "Hello there" in result.prompt
    assert "@transform" not in result.prompt


# --------------------------------------------------------------------------
# Tool adapter (Task A8)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_tool_spec_shape():
    spec = transform_tool_spec(transform_engine=TransformEngine(TransformRegistry()))
    assert spec.name == "transform"
    assert spec.parameters["required"] == ["operation", "content"]
    assert set(spec.parameters["properties"]) == {"operation", "content", "params"}


@pytest.mark.asyncio
async def test_transform_tool_handler_runs_engine():
    async def _fake(content, *, project_id=None, **params):
        return TransformResult(
            operation="summarize", output=f"S[{content}]", ok=True
        )

    spec = transform_tool_spec(transform_engine=_engine_with("summarize", _fake))
    out = await spec.handler(operation="summarize", content="hello")
    assert out == "S[hello]"


@pytest.mark.asyncio
async def test_transform_tool_handler_reports_failure():
    # Empty engine — "summarize" is unknown.
    spec = transform_tool_spec(transform_engine=TransformEngine(TransformRegistry()))
    out = await spec.handler(operation="summarize", content="x")
    assert "transform failed" in out
    assert "unknown" in out.lower()


# --------------------------------------------------------------------------
# End-to-end integration (Task A9)
# --------------------------------------------------------------------------


class _FakeLLM:
    """A duck-typed LLM client returning a fixed completion."""

    def __init__(self, text):
        self._text = text

    async def acompletion(self, *, messages, **_):
        return {"choices": [{"message": {"content": self._text}}]}


class _FakeContext:
    """A duck-typed context provider returning a fixed document."""

    def __init__(self, text):
        self._text = text

    async def retrieve(self, query, top_k=5):
        return [self._text]


class _FakeExtractor:
    def __init__(self, triples):
        self._triples = triples

    async def extract(self, text):
        return self._triples


class _FakeGraph:
    def __init__(self):
        self.calls = []

    async def add_relation(self, source, relation, target):
        self.calls.append((source, relation, target))


def _real_summarize_engine(llm):
    """A TransformEngine running the real ``summarize`` op against ``llm``."""
    reg = TransformRegistry()

    async def _op(content, *, project_id=None, **params):
        return await summarize(content, llm=llm, **params)

    reg.register("summarize", _op)
    return TransformEngine(reg)


@pytest.mark.asyncio
async def test_directive_end_to_end_literal_source():
    """@transform(summarize, "<text>") runs the real op and injects the summary."""
    engine = DirectiveEngine(model="gpt-4o")
    register_transform_directive(
        engine, transform_engine=_real_summarize_engine(_FakeLLM("THE SUMMARY"))
    )
    result = await engine.resolve(
        '@transform(summarize, "a very long document") What does it say?'
    )
    assert "THE SUMMARY" in result.prompt
    assert "What does it say?" in result.prompt


@pytest.mark.asyncio
async def test_directive_source_uses_raw_resolved_content():
    """The transform sees the raw resolved source text, not the formatted prompt."""
    captured: dict = {}

    async def _echo(content, *, project_id=None, **params):
        captured["content"] = content
        return TransformResult(operation="echo", output="ok", ok=True)

    # A small-model profile would compress the formatted prompt — the raw
    # resolved-directive content must still reach the operation verbatim.
    engine = DirectiveEngine(
        context=_FakeContext("RAW-CONTEXT-BODY"), model="ollama/llama3.2:3b"
    )
    register_transform_directive(
        engine, transform_engine=_engine_with("echo", _echo)
    )
    await engine.resolve("@transform(echo, @context('doc')) go")
    assert captured["content"] == "RAW-CONTEXT-BODY"


@pytest.mark.asyncio
async def test_directive_end_to_end_nested_directive_source():
    """The source may be a nested directive the engine resolves first."""
    engine = DirectiveEngine(
        context=_FakeContext("the resolved context body"), model="gpt-4o"
    )
    register_transform_directive(
        engine, transform_engine=_real_summarize_engine(_FakeLLM("NESTED SUMMARY"))
    )
    result = await engine.resolve(
        "@transform(summarize, @context('mission history')) Carry on"
    )
    assert "NESTED SUMMARY" in result.prompt
    assert "Carry on" in result.prompt


@pytest.mark.asyncio
async def test_tool_end_to_end_graphify_writes_relations():
    """The transform tool runs graphify and writes relations to the graph."""
    triples = [
        SimpleNamespace(subject="Alert", predicate="triggered-by", object="DeployX"),
        SimpleNamespace(subject="DeployX", predicate="owned-by", object="TeamA"),
    ]
    graph = _FakeGraph()

    reg = TransformRegistry()

    async def _graphify_op(content, *, project_id=None, **params):
        return await graphify(
            content, extractor=_FakeExtractor(triples), graph=graph
        )

    reg.register("graphify", _graphify_op)
    spec = transform_tool_spec(transform_engine=TransformEngine(reg))

    out = await spec.handler(operation="graphify", content="incident transcript")
    assert graph.calls == [
        ("Alert", "triggered-by", "DeployX"),
        ("DeployX", "owned-by", "TeamA"),
    ]
    assert "2 relations" in out
