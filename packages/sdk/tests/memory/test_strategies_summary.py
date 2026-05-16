# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
import pytest
from sagewai.memory.strategies.base import TurnEvent
from sagewai.memory.strategies.summary import SummaryStrategy


class _FakeLLM:
    async def acompletion(self, *, messages, **_):
        return {"choices": [{"message": {"content": "User asked about onboarding; assistant explained setup."}}]}


@pytest.mark.asyncio
async def test_summary_produces_one_record_per_session():
    strat = SummaryStrategy(llm=_FakeLLM())
    out = await strat.extract([
        TurnEvent(role="user", content="how do I onboard?", session_id="s1"),
        TurnEvent(role="assistant", content="run just bootstrap.", session_id="s1"),
    ])
    assert len(out) == 1
    rec = out[0]
    assert rec.namespace == "summaries"
    assert rec.strategy == "summary"
    assert "onboarding" in rec.content
