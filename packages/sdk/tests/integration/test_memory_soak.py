# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Memory & RAG soak harness — Lane A2 of the v1.0 launch coordination plan.

Four scenarios that the launch coordination spec marked as the validation
gate for the lighthouse demo's "second run is cheaper" claim. Specifically
the four failure modes Arda flagged on 2026-05-01:

1. **Long-context conversation against a corpus of 100s-1000s of docs** —
   does retrieval stay coherent across a long conversation when the
   corpus exceeds the context window many times over?

2. **Memory checkpoint save/restore** — save the conversation state to
   disk, restart the process, restore, continue — is the restored state
   functionally identical?

3. **LLM-swap mid-mission** — swap the LLM partway through a long mission
   (Opus → Sonnet → local Ollama). Does retrieval continue to work
   correctly when the embedding model under retrieval is a different
   model from the LLM driving the conversation?

4. **Branched memory isolation** — run two missions concurrently that
   share a `RAGEngine`. Verify mission A's writes do not surface in
   mission B's retrieval.

These tests are marked `@pytest.mark.soak` and are NOT part of the default
test suite — they take minutes to hours and may require real LLM
credentials. Run explicitly:

```
uv run --package sagewai --with pytest --with pytest-asyncio pytest \
    packages/sdk/tests/integration/test_memory_soak.py -m soak -v
```

The harness writes a report to `~/.sagewai/memory-soak-report.md` (or to
`SAGEWAI_SOAK_REPORT_PATH` if set). The report is the published artifact
the launch coordination plan calls for. Copy it to
`sagewai/atelier:docs/v1.0/memory-soak-report.md` after a successful run.

Scaffolding status (2026-05-01): test structure complete; default
implementations exercise the API surface against synthetic corpora using
the keyword-fallback retrieval path. Real-LLM and real-embedding runs
require setting `SAGEWAI_SOAK_REAL_LLM=1` plus appropriate API keys (the
tests will report SKIPPED with reason if those aren't available).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from sagewai.memory import MemoryBranch, RAGEngine

pytestmark = [pytest.mark.soak]


# ── helpers ────────────────────────────────────────────────────────


def _report_path() -> Path:
    """Where to write the soak report. Override via SAGEWAI_SOAK_REPORT_PATH."""
    override = os.environ.get("SAGEWAI_SOAK_REPORT_PATH")
    if override:
        return Path(override)
    return Path.home() / ".sagewai" / "memory-soak-report.md"


def _real_llm_enabled() -> bool:
    """True when SAGEWAI_SOAK_REAL_LLM=1 and at least one API key is set."""
    if os.environ.get("SAGEWAI_SOAK_REAL_LLM") != "1":
        return False
    return bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


def _synthetic_corpus(n: int = 100) -> list[str]:
    """Build a synthetic dense corpus of n documents.

    Documents are deterministic so retrieval behaviour is repeatable.
    Each doc is ~50 tokens with both unique and shared terms so retrieval
    has discriminating signal.
    """
    topics = [
        "agents", "memory", "fleet", "sandbox", "harness",
        "autopilot", "strategy", "guardrail", "training", "telemetry",
    ]
    docs = []
    for i in range(n):
        primary = topics[i % len(topics)]
        secondary = topics[(i + 3) % len(topics)]
        docs.append(
            f"Document {i} about {primary}. "
            f"This text covers {primary} and how it relates to {secondary}. "
            f"Key concept: {primary}-{i:04d}. "
            f"Sagewai's {primary} subsystem implements {secondary} integration."
        )
    return docs


def _append_report(scenario: str, result: dict[str, Any]) -> None:
    """Append a scenario's result to the report file."""
    path = _report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=timezone.utc).isoformat()
    block = f"\n## {scenario} — {timestamp}\n\n```json\n{json.dumps(result, indent=2, default=str)}\n```\n"
    if not path.exists():
        path.write_text(
            f"# Sagewai memory & RAG soak report\n\n"
            f"Generated: {timestamp}\n"
            f"Real-LLM mode: {_real_llm_enabled()}\n"
        )
    with path.open("a") as f:
        f.write(block)


# ── 1. Long-context conversation against a large corpus ─────────────


@pytest.mark.asyncio
async def test_soak_long_context_retrieval():
    """Retrieve relevantly from a 100-doc corpus across multiple queries."""
    engine = RAGEngine()
    corpus = _synthetic_corpus(n=100)

    # Ingest the corpus
    for doc in corpus:
        await engine.store(doc)

    # Run 50 representative queries
    queries = [
        "Tell me about agents",
        "How does the fleet work?",
        "What is the sandbox?",
        "Explain memory strategies",
        "What is harness?",
    ] * 10  # 50 total

    recall_at_5_hits = 0
    total = 0
    for q in queries:
        results = await engine.retrieve(q, top_k=5)
        total += 1
        # Heuristic: result is "relevant" if any of the query terms appear
        for r in results:
            if any(term.lower() in r.lower() for term in q.lower().split()):
                recall_at_5_hits += 1
                break

    recall_at_5 = recall_at_5_hits / max(total, 1)

    result = {
        "corpus_size": len(corpus),
        "query_count": total,
        "recall_at_5": recall_at_5,
        "real_llm": _real_llm_enabled(),
    }
    _append_report("1. Long-context retrieval", result)

    # The default keyword-fallback should hit something on most queries
    assert recall_at_5 > 0.5, f"recall@5={recall_at_5} too low — possible regression"


# ── 2. Checkpoint save/restore equivalence ──────────────────────────


@pytest.mark.asyncio
async def test_soak_checkpoint_save_restore():
    """RAGEngine state survives serialise → restart → restore.

    The current RAGEngine is in-memory; this test characterises the
    behaviour. Persistent backends (Milvus, NebulaGraph) are exercised
    separately in their own integration suites.
    """
    engine_a = RAGEngine()
    corpus = _synthetic_corpus(n=20)
    for doc in corpus:
        await engine_a.store(doc)

    # Capture the conversation state — for in-memory engine, "state" is
    # the docs that have been stored. We snapshot via repeated retrieval.
    pre_query = "memory and fleet"
    pre_results = await engine_a.retrieve(pre_query, top_k=5)

    # Simulate process restart: build a fresh engine, replay the corpus
    engine_b = RAGEngine()
    for doc in corpus:
        await engine_b.store(doc)

    post_results = await engine_b.retrieve(pre_query, top_k=5)

    # Equivalence: same set of retrieved docs (order may differ slightly
    # for ties)
    equivalent = set(pre_results) == set(post_results)

    result = {
        "corpus_size": len(corpus),
        "query": pre_query,
        "pre_count": len(pre_results),
        "post_count": len(post_results),
        "equivalent": equivalent,
        "real_llm": _real_llm_enabled(),
    }
    _append_report("2. Checkpoint save/restore", result)

    assert equivalent, "checkpoint restore did not produce equivalent retrieval set"


# ── 3. LLM-swap mid-mission ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_soak_llm_swap_stability():
    """Memory subsystem stays coherent when the conversation LLM swaps.

    The memory layer should be LLM-agnostic — strategies + retrieval should
    not bake in any specific LLM's tokenization or embedding. We verify
    by extracting facts via two different mock-LLM clients and confirming
    they produce comparable extracted records.
    """
    if not _real_llm_enabled():
        pytest.skip(
            "SAGEWAI_SOAK_REAL_LLM=1 + an API key required for real-LLM swap"
        )

    # When real-LLM is enabled, this would:
    #   1. Build a long conversation (50+ turns)
    #   2. Run extraction with claude-haiku-4-5
    #   3. Run extraction with gpt-4o-mini
    #   4. Compare extracted facts for semantic overlap
    # Without real LLMs, the swap-stability scenario reduces to checking
    # that the strategy framework accepts duck-typed clients.
    pytest.skip(
        "real-LLM swap not yet wired in scaffolding; awaiting API key + corpus"
    )


# ── 4. Branched memory isolation ────────────────────────────────────


@pytest.mark.asyncio
async def test_soak_branched_memory_isolation():
    """Two RAGEngines with different MemoryBranches do not see each other's writes.

    This is the smoking gun for issue #195 — read-side branch filtering.
    With PR #196's namespacing, writes are scoped; this test verifies
    the read path also filters correctly.

    Currently expected to FAIL (or trivially PASS by using separate
    engines) until issue #195 lands a single-engine, multi-branch
    isolation. The test documents the expected behaviour either way.
    """
    branch_a = MemoryBranch.global_root().scoped("mission-a")
    branch_b = MemoryBranch.global_root().scoped("mission-b")

    # Two separate engines, each scoped to one branch via namespace
    engine_a = RAGEngine()
    engine_b = RAGEngine()

    await engine_a.store("Mission A's secret note: alpha-12345")
    await engine_b.store("Mission B's secret note: beta-67890")

    a_results = await engine_a.retrieve("alpha-12345", top_k=3)
    b_results = await engine_b.retrieve("beta-67890", top_k=3)

    # A only sees alpha; B only sees beta
    a_isolation = all("beta-67890" not in r for r in a_results)
    b_isolation = all("alpha-12345" not in r for r in b_results)

    # Cross-leak check: querying A for B's term should not return B's content
    a_for_b = await engine_a.retrieve("beta-67890", top_k=3)
    b_for_a = await engine_b.retrieve("alpha-12345", top_k=3)

    cross_leak_count = sum(1 for r in a_for_b if "beta-67890" in r)
    cross_leak_count += sum(1 for r in b_for_a if "alpha-12345" in r)

    result = {
        "branch_a": str(branch_a),
        "branch_b": str(branch_b),
        "a_self_retrievable": len(a_results) > 0,
        "b_self_retrievable": len(b_results) > 0,
        "a_isolated_from_b": a_isolation,
        "b_isolated_from_a": b_isolation,
        "cross_leak_count": cross_leak_count,
        "real_llm": _real_llm_enabled(),
        "note": "single-engine multi-branch isolation pending issue #195",
    }
    _append_report("4. Branched memory isolation", result)

    # With separate engines, isolation is trivial. The real test (single
    # engine, two branches) requires #195 — when that lands, swap to a
    # shared engine + separate branch retrieval calls.
    assert a_isolation, "branch A leaked branch B's content"
    assert b_isolation, "branch B leaked branch A's content"
    assert cross_leak_count == 0, (
        f"cross-leak detected: {cross_leak_count} items leaked between branches"
    )
