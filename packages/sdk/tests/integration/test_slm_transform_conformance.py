# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SLM conformance tests — the transform capability must work on a cheap local model.

This is the moat claim for the transform capability: ``graphify`` and
``summarize`` must produce usable results on a locally-hosted small language
model, not just frontier APIs. ``graphify`` extraction is ``parse_json``-robust,
so a fenced SLM response still yields clean triples.

Opt-in via:
    SAGEWAI_SLM_CONFORMANCE=1

Requires a running Ollama instance with the ``llama3.2`` model pulled:
    ollama pull llama3.2

Skipped automatically when the flag is absent — intentional in normal CI runs.
"""

from __future__ import annotations

import os

import pytest

from sagewai.transform.operations import graphify, summarize

pytestmark = [pytest.mark.soak]

_SLM_MODEL = "ollama/llama3.2:latest"


def _slm_enabled() -> bool:
    return os.environ.get("SAGEWAI_SLM_CONFORMANCE") == "1"


_SKIP_REASON = (
    "SLM conformance test skipped. "
    "Set SAGEWAI_SLM_CONFORMANCE=1 with Ollama + llama3.2 to run."
)

# Relation-rich text — a competent extractor should pull several triples.
_GRAPHIFY_TEXT = (
    "Alice manages the payments team. The payments service depends on the "
    "auth service. Bob owns the auth service. The billing service depends "
    "on the payments service."
)

# A longer passage for summarize to compress.
_SUMMARIZE_TEXT = (
    "The quarterly infrastructure review covered four areas. First, the "
    "payments platform migrated to a new connection-pool configuration that "
    "reduced tail latency by thirty percent. Second, the auth service "
    "adopted short-lived signing certificates after a prior expiry incident. "
    "Third, the observability stack moved from remote-write to a scrape "
    "model, which stopped silently dropping histogram metrics. Fourth, the "
    "on-call rotation adopted a knowledge-graph of past incidents so that "
    "repeated root causes surface automatically during triage."
)


@pytest.mark.asyncio
async def test_summarize_runs_on_slm() -> None:
    """summarize must return ok=True with non-empty output on a local SLM."""
    if not _slm_enabled():
        pytest.skip(_SKIP_REASON)

    result = await summarize(_SUMMARIZE_TEXT, model=_SLM_MODEL, max_words=60)

    assert result.ok, f"summarize failed on SLM: {result.error}"
    assert result.output.strip(), "summarize returned empty output on SLM"


@pytest.mark.asyncio
async def test_graphify_runs_on_slm() -> None:
    """graphify must return ok=True with non-empty output on a local SLM.

    Exercises the full path: LLMRelationExtractor → litellm → Ollama →
    parse_json → GraphMemory. A fenced SLM response still parses.
    """
    if not _slm_enabled():
        pytest.skip(_SKIP_REASON)

    result = await graphify(
        _GRAPHIFY_TEXT, model=_SLM_MODEL, project_id="slm-transform-conformance"
    )

    assert result.ok, f"graphify failed on SLM: {result.error}"
    assert result.output.strip(), "graphify returned empty output on SLM"
    assert "relations_written" in result.metadata
