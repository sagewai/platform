# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the built-in transform operations."""

from types import SimpleNamespace

import pytest

from sagewai.transform.operations import graphify, summarize


class _FakeLLM:
    async def acompletion(self, *, messages, **_):
        return {"choices": [{"message": {"content": "a short summary"}}]}


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


@pytest.mark.asyncio
async def test_summarize_returns_result():
    result = await summarize("long text " * 100, llm=_FakeLLM(), max_words=50)
    assert result.ok
    assert result.operation == "summarize"
    assert result.output == "a short summary"


@pytest.mark.asyncio
async def test_graphify_writes_relations():
    triples = [
        SimpleNamespace(subject="Alert", predicate="triggered-by", object="DeployX"),
        SimpleNamespace(subject="DeployX", predicate="owned-by", object="TeamA"),
    ]
    graph = _FakeGraph()
    result = await graphify("text", extractor=_FakeExtractor(triples), graph=graph)
    assert result.ok
    assert result.operation == "graphify"
    assert result.metadata == {"relations_written": 2}
    assert graph.calls == [
        ("Alert", "triggered-by", "DeployX"),
        ("DeployX", "owned-by", "TeamA"),
    ]


@pytest.mark.asyncio
async def test_graphify_zero_triples_is_ok():
    graph = _FakeGraph()
    result = await graphify("text", extractor=_FakeExtractor([]), graph=graph)
    assert result.ok
    assert result.metadata == {"relations_written": 0}
    assert "no relations" in result.output.lower()
    assert graph.calls == []


@pytest.mark.asyncio
async def test_graphify_handles_fenced_json_from_slm(monkeypatch):
    """A fenced-JSON SLM response still yields clean triples via parse_json."""
    fenced = '```json\n[["Alert", "triggered-by", "DeployX"]]\n```'

    async def _fake_acompletion(**_):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=fenced))]
        )

    import litellm

    monkeypatch.setattr(litellm, "acompletion", _fake_acompletion)
    graph = _FakeGraph()
    result = await graphify("Alert text", graph=graph)
    assert result.ok
    assert result.metadata == {"relations_written": 1}
    assert graph.calls == [("Alert", "triggered-by", "DeployX")]
