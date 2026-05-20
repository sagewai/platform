# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.llm."""
from types import SimpleNamespace

import pytest

from sagewai.tools.builtins import llm as llm_mod


@pytest.mark.asyncio
async def test_content_translate_uses_low_complexity_and_target_lang(monkeypatch):
    captured = {}

    async def fake_chat(messages, *, complexity_hint):
        captured["messages"] = messages
        captured["complexity_hint"] = complexity_hint
        return SimpleNamespace(content="Hallo Welt", metadata={"source_lang": "en"})

    monkeypatch.setattr(llm_mod, "_chat_completion", fake_chat)
    out = await llm_mod.content_translate({
        "text": "Hello world", "target_lang": "German",
    })
    assert out["translated"] == "Hallo Welt"
    assert out["source_lang_detected"] == "en"
    assert captured["complexity_hint"] == "low"
    user_msg = captured["messages"][0]["content"]
    assert "German" in user_msg
    assert "Hello world" in user_msg


@pytest.mark.asyncio
async def test_content_translate_truncates_long_input(monkeypatch):
    captured = {}

    async def fake_chat(messages, *, complexity_hint):
        captured["len"] = len(messages[0]["content"])
        return SimpleNamespace(content="x", metadata=None)

    monkeypatch.setattr(llm_mod, "_chat_completion", fake_chat)
    long_text = "a" * 50_000
    await llm_mod.content_translate({"text": long_text, "target_lang": "fr"})
    assert captured["len"] <= 16_500  # 16k cap + prompt template overhead < 500


@pytest.mark.asyncio
async def test_quiz_generate_parses_json_array(monkeypatch):
    fake_json = (
        '[{"q": "What is 2+2?", "choices": ["3","4","5","6"], '
        '"answer": "4", "explanation": "basic arithmetic"}]'
    )

    async def fake_chat(messages, *, complexity_hint):
        assert complexity_hint == "medium"
        return SimpleNamespace(content=fake_json, metadata=None)

    monkeypatch.setattr(llm_mod, "_chat_completion", fake_chat)
    out = await llm_mod.quiz_generate({
        "topic": "arithmetic", "num_questions": 1, "difficulty": "easy",
    })
    assert len(out["questions"]) == 1
    assert out["questions"][0]["answer"] == "4"


@pytest.mark.asyncio
async def test_quiz_generate_propagates_parse_error(monkeypatch):
    async def fake_chat(messages, *, complexity_hint):
        return SimpleNamespace(content="not json at all", metadata=None)

    monkeypatch.setattr(llm_mod, "_chat_completion", fake_chat)
    with pytest.raises(Exception):
        await llm_mod.quiz_generate({"topic": "x"})
