# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
import pytest
from sagewai.memory.strategies.base import TurnEvent
from sagewai.memory.strategies.semantic import SemanticFactStrategy


class _FakeLLM:
    async def acompletion(self, *, messages, **_):
        # Strategy must produce JSON list of facts
        return {"choices": [{"message": {"content": '["user lives in Berlin", "user is a software engineer"]'}}]}


@pytest.mark.asyncio
async def test_extracts_facts_into_semantic_namespace():
    strat = SemanticFactStrategy(llm=_FakeLLM())
    turns = [
        TurnEvent(role="user", content="Hi, I'm a software engineer in Berlin.", session_id="s1"),
        TurnEvent(role="assistant", content="Nice to meet you.", session_id="s1"),
    ]
    out = await strat.extract(turns)
    assert len(out) == 2
    assert all(r.namespace == "semantic" for r in out)
    assert all(r.strategy == "semantic" for r in out)
    assert any("Berlin" in r.content for r in out)


@pytest.mark.asyncio
async def test_empty_turns_returns_no_records():
    out = await SemanticFactStrategy(llm=_FakeLLM()).extract([])
    assert out == []


class _FencedLLM:
    """Mimics models (Anthropic in particular) that wrap JSON in a fence."""

    async def acompletion(self, *, messages, **_):
        return {
            "choices": [
                {"message": {"content": '```json\n["user lives in Berlin"]\n```'}}
            ]
        }


@pytest.mark.asyncio
async def test_extracts_facts_from_fenced_json():
    """A Markdown-fenced JSON response is still parsed — strategies must be
    LLM-agnostic. Regression guard for the soak scenario-3 finding."""
    out = await SemanticFactStrategy(llm=_FencedLLM()).extract(
        [TurnEvent(role="user", content="I live in Berlin.", session_id="s1")]
    )
    assert len(out) == 1
    assert "Berlin" in out[0].content
