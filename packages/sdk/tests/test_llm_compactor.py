# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for LLMCompactor — LLM-powered abstractive summarization."""

from unittest.mock import AsyncMock, patch

import pytest

from sagewai.core.compactor import LLMCompactor, PromptCompactor
from sagewai.models.message import ChatMessage


class TestLLMCompactor:
    def test_is_subclass_of_prompt_compactor(self):
        compactor = LLMCompactor(max_tokens=100, model="gpt-4o-mini")
        assert isinstance(compactor, PromptCompactor)

    @pytest.mark.asyncio
    async def test_summarize_calls_llm(self):
        compactor = LLMCompactor(max_tokens=100, model="gpt-4o-mini")
        messages = [
            ChatMessage.user("What is Python?"),
            ChatMessage.assistant("Python is a programming language."),
            ChatMessage.user("What about JavaScript?"),
            ChatMessage.assistant("JavaScript is used for web development."),
        ]

        with patch("sagewai.core.compactor._call_summary_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "User asked about Python and JavaScript. Agent explained both."
            result = await compactor.summarize_async(messages)

        assert "Python" in result
        assert "JavaScript" in result
        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_compact_async_uses_llm_summary(self):
        compactor = LLMCompactor(max_tokens=20, preserve_recent=2, model="gpt-4o-mini")
        messages = [
            ChatMessage.system("You are helpful."),
            ChatMessage.user("msg 1"),
            ChatMessage.assistant("reply 1"),
            ChatMessage.user("msg 2"),
            ChatMessage.assistant("reply 2"),
            ChatMessage.user("msg 3"),
            ChatMessage.assistant("reply 3"),
        ]

        with patch("sagewai.core.compactor._call_summary_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "Summary of earlier conversation."
            result = await compactor.compact_async(messages)

        # System prompt + summary + 2 recent messages
        assert len(result) == 4
        assert result[0].role.value == "system"
        assert result[1].content is not None
        assert "Summary" in result[1].content

    @pytest.mark.asyncio
    async def test_compact_async_skips_if_under_threshold(self):
        compactor = LLMCompactor(max_tokens=100000, model="gpt-4o-mini")
        messages = [ChatMessage.user("short")]
        result = await compactor.compact_async(messages)
        assert result == messages

    def test_sync_compact_falls_back_to_extractive(self):
        """The sync compact() method still uses extractive summarization."""
        compactor = LLMCompactor(max_tokens=50, preserve_recent=1, model="gpt-4o-mini")
        messages = [
            ChatMessage.user("a" * 300),
            ChatMessage.assistant("b" * 300),
            ChatMessage.user("recent"),
        ]
        result = compactor.compact(messages)
        # Should still work (extractive fallback)
        assert len(result) >= 2
