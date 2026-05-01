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
from sagewai.memory.strategies.preference import PreferenceStrategy


class _FakeLLM:
    async def acompletion(self, *, messages, **_):
        return {"choices": [{"message": {"content": '[{"key": "tone", "value": "concise"}]'}}]}


@pytest.mark.asyncio
async def test_extracts_preference_kv_into_preferences_namespace():
    strat = PreferenceStrategy(llm=_FakeLLM())
    out = await strat.extract([
        TurnEvent(role="user", content="Keep your replies short.", session_id="s1"),
    ])
    assert len(out) == 1
    rec = out[0]
    assert rec.namespace == "preferences"
    assert rec.metadata["key"] == "tone"
    assert rec.metadata["value"] == "concise"


@pytest.mark.asyncio
async def test_invalid_shape_drops_records():
    class _Bad:
        async def acompletion(self, *, messages, **_):
            return {"choices": [{"message": {"content": "not json"}}]}
    out = await PreferenceStrategy(llm=_Bad()).extract([
        TurnEvent(role="user", content="x", session_id="s1"),
    ])
    assert out == []
