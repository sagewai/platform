# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for hallucination detection guardrail."""

from __future__ import annotations

import pytest

from sagewai.safety.hallucination import HallucinationGuard


class TestHallucinationDetection:
    """Test hallucination detection via self-consistency."""

    @pytest.mark.asyncio
    async def test_passes_grounded_response(self):
        """Response grounded in context passes."""
        guard = HallucinationGuard(threshold=0.5)
        context = {"rag_context": ["Paris is the capital of France."]}
        result = await guard.check_output("The capital of France is Paris.", context)
        assert result.passed

    @pytest.mark.asyncio
    async def test_flags_no_context(self):
        """Without RAG context, guard passes (cannot verify)."""
        guard = HallucinationGuard(threshold=0.5)
        result = await guard.check_output("Some claim.", {})
        assert result.passed  # No context to check against

    @pytest.mark.asyncio
    async def test_input_always_passes(self):
        """Input check always passes (hallucination is output concern)."""
        guard = HallucinationGuard()
        result = await guard.check_input("Any input", {})
        assert result.passed

    @pytest.mark.asyncio
    async def test_low_grounding_score_blocks(self):
        """Response with zero overlap to context triggers violation."""
        guard = HallucinationGuard(threshold=0.7, action="block")
        context = {"rag_context": ["Python was created by Guido van Rossum."]}
        result = await guard.check_output(
            "JavaScript was invented by Brendan Eich at Netscape.", context
        )
        # Low grounding score — different topic entirely
        assert not result.passed

    @pytest.mark.asyncio
    async def test_warn_action(self):
        guard = HallucinationGuard(threshold=0.7, action="warn")
        context = {"rag_context": ["The sky is blue."]}
        result = await guard.check_output("The ocean is green and purple.", context)
        if not result.passed:
            assert result.action == "warn"

    @pytest.mark.asyncio
    async def test_grounding_score_calculation(self):
        """Verify grounding score is reasonable for partial overlap."""
        guard = HallucinationGuard(threshold=0.3)
        context = {"rag_context": ["Python is a programming language created by Guido."]}
        # Partial overlap — mentions Python and programming
        result = await guard.check_output(
            "Python is a popular programming language used worldwide.", context
        )
        assert result.passed

    @pytest.mark.asyncio
    async def test_empty_response_passes(self):
        guard = HallucinationGuard(threshold=0.5)
        context = {"rag_context": ["Some context."]}
        result = await guard.check_output("", context)
        assert result.passed

    @pytest.mark.asyncio
    async def test_escalate_action(self):
        guard = HallucinationGuard(threshold=0.9, action="escalate")
        context = {"rag_context": ["Cats are mammals."]}
        result = await guard.check_output("Dogs are reptiles from Mars.", context)
        assert not result.passed
        assert result.action == "escalate"
