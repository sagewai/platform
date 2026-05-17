#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Example 50 — Incident knowledge graph (the @transform directive).

An on-call agent triages incident after incident, but each one is
investigated from scratch: the connections it discovers — which deploy
caused which alert, which service depends on which — stay buried in
transcripts and are never reused.

This example fixes that with a single directive. After an incident-triage
run, ``@transform(graphify, @context('incident transcript'))`` distils the
transcript into relational triples — ``PaymentsHighErrorRate -> triggered-by
-> deploy-2026-05-10-payments``, ``payments -> depends-on -> auth`` — and
writes them into the project-scoped :class:`~sagewai.memory.graph.GraphMemory`.

When a second, related incident fires, the agent does not re-read past
transcripts. It retrieves the connected sub-graph straight from memory —
the prior deploy, the shared dependency, the known root cause — and triages
faster.

The transform runs **before** the LLM call (the directive engine executes
it during prompt resolution), and on a small/cheap model. The engine *does*
the graphify work declaratively — the model never has to choose to.

Offline by default
------------------
With no flag set, the example uses a deterministic stub relation-extractor,
so it runs end-to-end with no API key and no network. Set
``SAGEWAI_TRANSFORM_LIVE=1`` (and an LLM API key) to graphify the transcripts
with the real :class:`~sagewai.intelligence.extractors.llm_extractor.LLMRelationExtractor`.

What's exercised
----------------
- ``@transform(graphify, ...)`` — the transform directive, resolved + executed pre-LLM
- :func:`~sagewai.transform.register_transform_directive` — the directive adapter
- :class:`~sagewai.transform.TransformEngine` / :func:`~sagewai.transform.graphify`
- :class:`~sagewai.memory.graph.GraphMemory` — project-scoped relation store + traversal
- :class:`~sagewai.directives.engine.DirectiveEngine` — nested ``@context`` source resolution

Usage::

    python 50_incident_knowledge_graph.py
    SAGEWAI_TRANSFORM_LIVE=1 OPENAI_API_KEY=sk-... python 50_incident_knowledge_graph.py
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from sagewai.directives.engine import DirectiveEngine
from sagewai.memory.graph import GraphMemory
from sagewai.transform import (
    TransformEngine,
    TransformRegistry,
    graphify,
    register_transform_directive,
)

PROJECT_ID = "incident-kg"


# ── synthetic incident transcripts ───────────────────────────────────


# Incident 1 — investigated from scratch. Its transcript is graphified.
TRANSCRIPT_1 = """\
INCIDENT INC-4471 — Payments API 5xx error-rate spike
Severity: SEV-2  |  Opened: 2026-05-10 14:02 UTC

[14:02] PagerDuty alert "PaymentsHighErrorRate" fired — the 5xx rate on the
        payments service crossed 8%.
[14:06] On-call confirmed the spike started at 13:58, four minutes after the
        deploy "deploy-2026-05-10-payments" rolled out.
[14:11] deploy-2026-05-10-payments is owned by the payments-team; it bumped
        the database driver and shrank the connection-pool size.
[14:19] payments depends on the auth service for token validation; auth was
        healthy throughout and was ruled out as a cause.
[14:25] Root cause: connection-pool-exhaustion — the new pool was too small
        for peak checkout traffic and pool waits became 5xx timeouts.
[14:31] Mitigated by reverting the pool-size change; error rate back to
        baseline by 14:35.
"""


# Incident 2 — a related incident that arrives four days later.
TRANSCRIPT_2 = """\
INCIDENT INC-4488 — Billing invoice latency spike
Severity: SEV-2  |  Opened: 2026-05-14 09:40 UTC

[09:40] Alert "BillingLatencyHigh" fired — invoice p99 latency hit 6s.
[09:44] On-call notes the billing service calls into payments to settle
        invoices, so a payments regression would surface here.
[09:48] No billing deploy in the last 24h — the trigger is upstream.
"""


# ── deterministic stub extractor (offline mode) ──────────────────────


@dataclass(frozen=True)
class _Triple:
    """A minimal relation triple — the shape graphify consumes."""

    subject: str
    predicate: str
    object: str


# The triples a competent relation-extractor would pull from each
# transcript. Keyed on the incident id so one stub serves both runs.
_STUB_TRIPLES: dict[str, list[_Triple]] = {
    "INC-4471": [
        _Triple("PaymentsHighErrorRate", "triggered-by", "deploy-2026-05-10-payments"),
        _Triple("deploy-2026-05-10-payments", "owned-by", "payments-team"),
        _Triple("deploy-2026-05-10-payments", "affects-service", "payments"),
        _Triple("payments", "depends-on", "auth"),
        _Triple("INC-4471", "root-cause", "connection-pool-exhaustion"),
        _Triple("INC-4471", "affects-service", "payments"),
    ],
    "INC-4488": [
        _Triple("BillingLatencyHigh", "affects-service", "billing"),
        _Triple("billing", "depends-on", "payments"),
        _Triple("INC-4488", "affects-service", "billing"),
    ],
}


class _StubExtractor:
    """A deterministic stand-in for ``LLMRelationExtractor`` (offline mode)."""

    async def extract(self, text: str) -> list[_Triple]:
        for incident_id, triples in _STUB_TRIPLES.items():
            if incident_id in text:
                return triples
        return []


# ── context provider — serves transcripts to @context(...) ───────────


class _TranscriptContext:
    """A duck-typed context provider holding the current transcript."""

    def __init__(self) -> None:
        self.transcript = ""

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        return [self.transcript] if self.transcript else []


# ── transform engine wiring ──────────────────────────────────────────


def _build_transform_engine(graph: GraphMemory, live: bool) -> TransformEngine:
    """A TransformEngine whose ``graphify`` writes into ``graph``.

    Offline (default): a deterministic stub extractor. Live: the real
    ``LLMRelationExtractor`` — graphify builds it from the ``model`` param.
    """
    registry = TransformRegistry()

    if live:

        async def _graphify_op(content, *, project_id=None, **params):
            params.setdefault("model", "gpt-4o-mini")
            return await graphify(content, graph=graph, **params)

    else:
        extractor = _StubExtractor()

        async def _graphify_op(content, *, project_id=None, **params):
            return await graphify(content, extractor=extractor, graph=graph)

    registry.register("graphify", _graphify_op)
    return TransformEngine(registry)


# ── output helpers ───────────────────────────────────────────────────


def _rule(title: str = "") -> None:
    if title:
        print(f"\n─── {title} " + "─" * max(2, 64 - len(title)))
    else:
        print("─" * 70)


def _transform_digest(result) -> str:
    """The TransformResult.output the @transform directive injected.

    Read off the resolved directive (``source == "custom:transform"``) — this
    is the digest before the engine's per-model context compression, so the
    full triple preview is visible regardless of the target model profile.
    """
    for resolved in result.directives_found:
        if resolved.source == "custom:transform" and resolved.content:
            return resolved.content
    return "(transform produced no output)"


# ── main ─────────────────────────────────────────────────────────────


async def main() -> None:
    live = bool(os.environ.get("SAGEWAI_TRANSFORM_LIVE"))

    _rule()
    print(" Sagewai — incident knowledge graph (example 50, @transform directive)")
    _rule()
    print(f"  mode: {'LIVE (real LLM extraction)' if live else 'offline (stub extractor)'}")
    print(f"  project: {PROJECT_ID}")

    # One project-scoped graph; one transform engine writing into it.
    graph = GraphMemory(max_depth=3, project_id=PROJECT_ID)
    transform_engine = _build_transform_engine(graph, live)

    # The context provider feeds the transcript to a nested @context(...).
    context = _TranscriptContext()
    # A deliberately small model — the transform runs on, and is usable by, SLMs.
    engine = DirectiveEngine(context=context, model="ollama/llama3.2:3b")
    register_transform_directive(engine, transform_engine=transform_engine)

    # ── Incident 1: triage, then graphify the transcript ──
    _rule("Incident 1 — INC-4471 (investigated from scratch)")
    print(TRANSCRIPT_1)
    context.transcript = TRANSCRIPT_1
    result = await engine.resolve(
        "@transform(graphify, @context('incident transcript'))"
    )
    print("  @transform(graphify, @context('incident transcript')) →")
    print(f"    {_transform_digest(result)}")
    print(f"  graph now holds {len(graph)} entities.")

    # ── Incident 2: retrieve the connected sub-graph BEFORE re-reading ──
    _rule("Incident 2 — INC-4488 (related; arrives four days later)")
    print(TRANSCRIPT_2)
    print("  Before re-reading any past transcript, the agent asks the graph:")
    probe = "billing latency — is payments or auth involved, and who owns recent deploys?"
    print(f'    query: "{probe}"')
    print()
    subgraph = await graph.retrieve(probe, top_k=12)
    print("  connected sub-graph retrieved from memory:")
    for line in subgraph:
        print(f"    {line}")
    print()
    print("  → The agent already knows, without re-reading INC-4471:")
    print("      • billing → payments → auth is the dependency chain to check")
    print("      • payments saw connection-pool-exhaustion once before")
    print("      • payments-team owns recent payments deploys — loop them in")

    # ── Graphify incident 2 too — the knowledge graph accumulates ──
    _rule("The knowledge graph grows")
    context.transcript = TRANSCRIPT_2
    result = await engine.resolve(
        "@transform(graphify, @context('incident transcript'))"
    )
    print(f"  graphified INC-4488 → {_transform_digest(result)}")
    print(f"  graph now holds {len(graph)} entities across 2 incidents.")
    print()
    chain = await graph.retrieve("billing payments auth", top_k=12)
    print("  billing → payments → auth is now one connected component:")
    for line in chain:
        print(f"    {line}")

    _rule()
    print("  Each triaged incident leaves its structure behind. The next one")
    print("  starts from the graph, not from a blank page — and the transform")
    print("  that builds it runs declaratively, pre-LLM, on a small model.")
    _rule()


if __name__ == "__main__":
    asyncio.run(main())
