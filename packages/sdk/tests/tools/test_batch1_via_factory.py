# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end: catalog -> factory -> executor -> builtin for batch-1 tools."""
from types import SimpleNamespace

import pytest
import respx

from sagewai.tools import factory, registry
from sagewai.tools.builtins import llm as llm_mod


def _noop_creds(*, project_id, kind, id):
    return {}


@pytest.mark.asyncio
async def test_diff_text_via_factory():
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_noop_creds)
    out = await callables["diff_text"]({"a": "x\n", "b": "y\n"})
    assert out["equal"] is False
    assert "x" in out["diff"]


@pytest.mark.asyncio
@respx.mock
async def test_web_scrape_via_factory():
    respx.get("https://example.test/").respond(
        200,
        content=b"<html><head><title>T</title></head><body><p>Hi</p></body></html>",
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_noop_creds)
    out = await callables["web_scrape"]({"url": "https://example.test/"})
    assert out["status"] == 200


@pytest.mark.asyncio
async def test_content_translate_via_factory(monkeypatch):
    async def fake_chat(messages, *, complexity_hint):
        assert complexity_hint == "low"
        return SimpleNamespace(content="Hallo", metadata=None)

    monkeypatch.setattr(llm_mod, "_chat_completion", fake_chat)
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_noop_creds)
    out = await callables["content_translate"]({"text": "Hello", "target_lang": "de"})
    assert out["translated"] == "Hallo"
