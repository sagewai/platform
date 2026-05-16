#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Soak A — Memory + RAG across LLMs (atelier issue #9).

The memory + RAG layer is the moat that lets a 7B local LLM with a 4K
context window hold a long conversation as cleanly as Opus does, because
it never sees the whole conversation — just the focused slice that
matters right now. This soak proves it: the same fixed corpus and
fixed conversation, retrieved through TF-IDF and (when installed)
SentenceTransformer, drive the same set of vague references on several
LLMs of different sizes. Out the back come six numbers we can publish:

    1. recall@5 on a 100-doc synthetic corpus (TF-IDF baseline)
    2. recall@5 delta TF-IDF → SentenceTransformer (when installed)
    3. average focused-slice token count vs full-history token count
       across vague references (the Gap #5 scenario from Example 37)
    4. cross-LLM consistency on the same focused slice — accuracy,
       $/call, p50/p99 latency
    5. cross-tenant leak count between two project-scoped vector stores
    6. hallucination rate against a closed-domain corpus — refusal rate
       on out-of-scope questions

What's exercised:

- ``sagewai.memory.VectorMemory`` directly — TF-IDF cosine similarity
  baseline; project-scoped writes for the cross-tenant scenario
- ``sagewai.memory.RAGEngine`` for the long-corpus and Gap #5 paths
- ``sagewai.intelligence.embeddings.SentenceTransformerEmbedder``
  when ``sentence-transformers`` is installed via the ``intelligence``
  extra; otherwise scenario 2 logs a clean SKIPPED row
- ``litellm.acompletion`` for the cross-LLM consistency and
  hallucination scenarios; same call shape, different ``model`` string

Requirements::

    pip install sagewai
    # Optional, in any combination:
    #   - ANTHROPIC_API_KEY  → Claude Haiku 4.5
    #   - OPENAI_API_KEY     → GPT-4o-mini
    #   - ollama serve       → first locally-pulled chat model
    # Optional, for scenario 2:
    #   pip install 'sagewai[intelligence]'   # sentence-transformers

Usage::

    python -m sagewai.examples._soaks.memory_soak
    # or
    python packages/sdk/sagewai/examples/_soaks/memory_soak.py

Spend cap: every model run aborts when its per-model spend would cross
``$0.50``; the whole soak's hard cap is ``$2.00``. Both caps are well
under the issue's $10 budget so a CI re-run cannot accidentally burn
the user's account. Output JSON lands at
``$SAGEWAI_SOAK_RESULTS_PATH`` (default:
``~/.sagewai/memory-soak-results.json``). Paste the report into
``sagewai/atelier:docs/v1.0/memory-soak-report.md`` per the template
in this script's sibling README.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError

import litellm

from dotenv import load_dotenv

# Load Sagewai credentials early so os.environ checks below pick them up.
# Silently no-ops if the file doesn't exist (clean-machine path).
load_dotenv(Path.home() / ".sagewai" / ".env")

from sagewai.memory import RAGEngine, VectorMemory


# ── configuration ──────────────────────────────────────────────────


PER_MODEL_SPEND_CAP_USD = 0.50
TOTAL_SPEND_CAP_USD = 2.00
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
OLLAMA_REQUEST_TIMEOUT_S = 60.0
PAID_REQUEST_TIMEOUT_S = 30.0


# ── dataset 1: 100-doc synthetic corpus + 50 queries ───────────────


_CORPUS_TOPICS = (
    "agents", "memory", "fleet", "sandbox", "harness",
    "autopilot", "strategy", "guardrail", "training", "telemetry",
)


def _synthetic_corpus(n: int = 100) -> list[str]:
    """Build a deterministic synthetic dense corpus of n documents.

    Each doc is ~50 tokens with both unique and shared terms so retrieval
    has a discriminating signal but the bag-of-words tokeniser also has
    something to grip on.
    """
    docs = []
    for i in range(n):
        primary = _CORPUS_TOPICS[i % len(_CORPUS_TOPICS)]
        secondary = _CORPUS_TOPICS[(i + 3) % len(_CORPUS_TOPICS)]
        docs.append(
            f"Document {i} about {primary}. "
            f"This text covers {primary} and how it relates to {secondary}. "
            f"Key concept: {primary}-{i:04d}. "
            f"Sagewai's {primary} subsystem implements {secondary} integration."
        )
    return docs


CORPUS_QUERIES: list[str] = [
    "Tell me about agents",
    "How does the fleet work?",
    "What is the sandbox?",
    "Explain memory strategies",
    "What is harness?",
] * 10  # 50 total


# ── dataset 2: 14-turn conversation across 4 interleaved topics ────


CONVERSATION: list[tuple[str, str]] = [
    # ── Topic A: email triage ──
    ("user", "I want to build an email triage agent for customer support tickets."),
    ("assistant", "An email triage agent makes sense. Triage classifies each email, drafts replies for the simple ones, and escalates the rest."),
    ("user", "We get 200 emails a day. Half of them are the same five repetitive support questions."),
    ("assistant", "Then the email triage agent should auto-reply to the repetitive 50% and summarise the rest for a human reviewer."),
    # ── Topic B: Q3 hiring ──
    ("user", "Different subject — the Q3 hiring plan needs three frontend hires, two backend hires, one DevRel hire."),
    ("assistant", "On the Q3 hiring plan, the DevRel hire is the hardest to close. Hiring DevRel is hard."),
    ("user", "For the Q3 hiring plan, should we move fast on the DevRel hire or slow?"),
    # ── Topic C: AWS Lambda ──
    ("user", "Switching topics — let's discuss the AWS Lambda migration. Where are we on it?"),
    ("assistant", "On the AWS Lambda migration, cold-start is the blocker. Provisioned concurrency at level 1 absorbs cold-start cost."),
    ("user", "On Lambda costs, is AWS Lambda still cheaper than ECS at our request volume?"),
    ("assistant", "Yes — Lambda costs less than ECS below 100 sustained req/sec because Lambda has no control-plane cost."),
    # ── Topic D: weekend ──
    ("user", "Quick aside: Friday burger plans?"),
    ("assistant", "Always. 7pm at Bun & Patty?"),
    # ── Back to Topic B ──
    ("user", "Back to the Q3 hiring plan — let's offer 6 weeks notice on the DevRel hire."),
]

VAGUE_REFERENCES: list[tuple[str, str]] = [
    ("ok back to the email triage agent — what's the next step?", "Topic A — email triage"),
    ("remind me about the Q3 hiring plan",                         "Topic B — Q3 hiring"),
    ("what did we say about AWS Lambda costs?",                    "Topic C — Lambda"),
]


# ── dataset 3: closed-domain corpus + 10 hallucination probes ──────


HALLUCINATION_FACTS: list[str] = [
    "Sagewai's autopilot translates a plain-English goal into a blueprint and a mission.",
    "The memory subsystem uses TF-IDF cosine similarity by default, with sentence-transformers as an optional embedder.",
    "Fleet workers heartbeat every 30 seconds and are evicted from the registry after 90 seconds of silence.",
    "The Sealed spine encrypts vendor credentials at rest and scopes them to a workload identity.",
    "Curator collects training samples from agent runs and Promoter pushes a fine-tuned LoRA to Ollama.",
    "Sagewai's CLI is invoked as `sagewai`, not `sage` or `sw`.",
    "The admin panel runs at port 3000 by default and proxies to the backend at port 8000.",
    "Examples live under `packages/sdk/sagewai/examples/` and are numbered NN_short_name.py.",
    "The licence is AGPL-3.0-or-later with a commercial dual-licence option.",
    "Brand colours are defined in `apps/admin/app/brand-tokens.css` for both light and dark themes.",
]

IN_SCOPE_QUESTIONS: list[str] = [
    "How does Sagewai's memory layer score similarity by default?",
    "How do fleet workers tell the registry they're alive?",
    "What does Curator do with training samples?",
    "What format are Sagewai examples named?",
    "What licence is Sagewai released under?",
]

OUT_OF_SCOPE_QUESTIONS: list[str] = [
    "How many enterprise customers does Sagewai have?",
    "What is Sagewai's current monthly recurring revenue?",
    "Where is Sagewai's headquarters located?",
    "Who is the founder of Sagewai?",
    "What is the default scheduler interval for Sagewai jobs?",
]


# ── result records ─────────────────────────────────────────────────


@dataclass
class CorpusRetrievalReport:
    corpus_size: int
    query_count: int
    recall_at_5: float
    avg_results_returned: float


@dataclass
class EmbedderComparisonReport:
    embedder: str
    available: bool
    skip_reason: str | None
    recall_at_5: float | None
    avg_results_returned: float | None


@dataclass
class FocusedSliceReport:
    full_history_tokens: int
    avg_slice_tokens: int
    avg_reduction_pct: float
    per_reference: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CrossLLMReport:
    model: str
    samples_attempted: int
    samples_completed: int
    answers_referenced_correct_topic: int
    p50_latency_ms: float
    p99_latency_ms: float
    avg_cost_usd: float
    total_cost_usd: float
    failure_reason: str | None = None


@dataclass
class CrossTenantReport:
    tenant_a_self_retrievable: bool
    tenant_b_self_retrievable: bool
    a_isolated_from_b: bool
    b_isolated_from_a: bool
    cross_leak_count: int


@dataclass
class HallucinationReport:
    model: str
    in_scope_questions: int
    in_scope_grounded: int
    out_of_scope_questions: int
    out_of_scope_refused: int
    hallucination_rate: float
    avg_cost_usd: float
    total_cost_usd: float
    failure_reason: str | None = None


# ── helpers ────────────────────────────────────────────────────────


def _ollama_first_chat_model() -> str | None:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=1.5) as resp:
            data = json.loads(resp.read())
    except (URLError, OSError, json.JSONDecodeError):
        return None
    names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    chat = [n for n in names if not any(t in n.lower() for t in ("coder", "code"))]
    priority = ("llama3.2", "llama3.1", "llama3", "qwen2.5", "qwen2", "mistral", "phi3", "gemma2", "gemma")
    chat.sort(
        key=lambda n: (
            next((i for i, p in enumerate(priority) if n.lower().startswith(p)), len(priority)),
            n,
        )
    )
    return chat[0] if chat else None


def _selected_models() -> list[str]:
    """Resolve which model strings to run against, in order."""
    selected: list[str] = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        selected.append("claude-haiku-4-5-20251001")
    if os.environ.get("OPENAI_API_KEY"):
        selected.append("openai/gpt-4o-mini")
    olm = _ollama_first_chat_model()
    if olm:
        selected.append(f"ollama/{olm}")
    return selected


def _is_ollama(model: str) -> bool:
    return model.startswith("ollama/") or model.startswith("ollama_chat/")


def _safe_completion_cost(model: str, response: Any) -> float:
    if _is_ollama(model):
        return 0.0
    try:
        return float(litellm.completion_cost(completion_response=response))
    except Exception:
        return 0.0


def _est_tokens(text: str) -> int:
    """Rough token estimate — 4 chars ≈ 1 token. Good enough for a demo."""
    return max(1, len(text) // 4)


def _content_terms(text: str) -> list[str]:
    """Lowercased word tokens with punctuation stripped — for grading."""
    import re as _re
    return _re.findall(r"\w+", text.lower())


def _format_turn(idx: int, speaker: str, text: str) -> str:
    return f"[t{idx:02d} {speaker:9s}] {text}"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(pct / 100.0 * (len(s) - 1)))))
    return s[idx]


def _bar(frac: float, width: int = 18) -> str:
    filled = int(round(frac * width))
    return "█" * filled + "·" * (width - filled)


# ── scenario 1: long-corpus retrieval (TF-IDF baseline) ────────────


async def scenario_corpus_retrieval() -> CorpusRetrievalReport:
    """100-doc corpus, 50 queries, recall@5 on TF-IDF cosine similarity.

    Uses ``VectorMemory`` directly so the recall number is a clean
    measurement of TF-IDF retrieval — independent of ``RAGEngine``'s
    HYBRID strategy, which would otherwise cap vector results at
    ``int(top_k * vector_weight)`` and conflate the score with
    graph-store coverage (which is intentionally empty here).
    """
    store = VectorMemory(project_id="soak-corpus")
    corpus = _synthetic_corpus(n=100)
    for doc in corpus:
        await store.store(doc)

    hits = 0
    counts: list[int] = []
    for q in CORPUS_QUERIES:
        results = await store.retrieve(q, top_k=5)
        counts.append(len(results))
        terms = [t for t in _content_terms(q) if len(t) > 2]
        for r in results:
            if any(t in r.lower() for t in terms):
                hits += 1
                break

    recall = hits / max(len(CORPUS_QUERIES), 1)
    avg_returned = sum(counts) / max(len(counts), 1)
    return CorpusRetrievalReport(
        corpus_size=len(corpus),
        query_count=len(CORPUS_QUERIES),
        recall_at_5=recall,
        avg_results_returned=avg_returned,
    )


# ── scenario 2: TF-IDF vs SentenceTransformer ──────────────────────


class _STStore:
    """Minimal in-memory ST-backed retrieval shim for the soak.

    Purposefully *not* layered into VectorMemory — the SDK's vector
    store is hard-pinned to TF-IDF today. This shim lets the soak
    measure the recall delta we'd see if we wired ST in.
    """

    def __init__(self, embedder: Any) -> None:
        self._embedder = embedder
        self._docs: list[str] = []
        self._vectors: list[list[float]] = []

    async def store(self, content: str) -> None:
        vec = await self._embedder.embed_query(content)
        self._docs.append(content)
        self._vectors.append(vec)

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        if not self._docs:
            return []
        q_vec = await self._embedder.embed_query(query)
        scores: list[tuple[float, str]] = []
        for vec, doc in zip(self._vectors, self._docs):
            scores.append((self._cosine(q_vec, vec), doc))
        scores.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scores[:top_k]]

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)


async def scenario_embedder_comparison() -> EmbedderComparisonReport:
    """Same corpus, same queries, ST-backed cosine vs TF-IDF cosine."""
    try:
        from sagewai.intelligence.embeddings import SentenceTransformerEmbedder
        embedder = SentenceTransformerEmbedder()
    except ImportError:
        return EmbedderComparisonReport(
            embedder="sentence-transformers/all-MiniLM-L6-v2",
            available=False,
            skip_reason="sentence-transformers not installed (pip install 'sagewai[intelligence]')",
            recall_at_5=None,
            avg_results_returned=None,
        )
    except Exception as exc:
        return EmbedderComparisonReport(
            embedder="sentence-transformers/all-MiniLM-L6-v2",
            available=False,
            skip_reason=f"embedder load failed: {type(exc).__name__}: {exc}",
            recall_at_5=None,
            avg_results_returned=None,
        )

    store = _STStore(embedder)
    corpus = _synthetic_corpus(n=100)
    for doc in corpus:
        await store.store(doc)

    hits = 0
    counts: list[int] = []
    for q in CORPUS_QUERIES:
        results = await store.retrieve(q, top_k=5)
        counts.append(len(results))
        terms = [t for t in _content_terms(q) if len(t) > 2]
        for r in results:
            if any(t in r.lower() for t in terms):
                hits += 1
                break

    recall = hits / max(len(CORPUS_QUERIES), 1)
    avg_returned = sum(counts) / max(len(counts), 1)
    return EmbedderComparisonReport(
        embedder="sentence-transformers/all-MiniLM-L6-v2",
        available=True,
        skip_reason=None,
        recall_at_5=recall,
        avg_results_returned=avg_returned,
    )


# ── scenario 3: focused-slice token reduction (Gap #5) ─────────────


async def scenario_focused_slice() -> FocusedSliceReport:
    """Build the 14-turn conversation, retrieve focused slices for vague refs."""
    engine = RAGEngine()
    for idx, (speaker, text) in enumerate(CONVERSATION):
        await engine.store(_format_turn(idx, speaker, text))

    full_text = "\n".join(
        _format_turn(i, s, t) for i, (s, t) in enumerate(CONVERSATION)
    )
    full_tokens = _est_tokens(full_text)

    per_ref: list[dict[str, Any]] = []
    slice_tokens_list: list[int] = []
    for ref, expected in VAGUE_REFERENCES:
        slice_lines = await engine.retrieve(ref, top_k=3)
        slice_text = "\n".join(slice_lines)
        slice_tokens = _est_tokens(slice_text)
        slice_tokens_list.append(slice_tokens)
        per_ref.append({
            "reference": ref,
            "expected_topic": expected,
            "retrieved_count": len(slice_lines),
            "slice_tokens": slice_tokens,
            "reduction_pct": (1 - slice_tokens / full_tokens) * 100 if full_tokens else 0.0,
        })

    avg_slice = sum(slice_tokens_list) // max(len(slice_tokens_list), 1)
    avg_reduction = (1 - avg_slice / full_tokens) * 100 if full_tokens else 0.0
    return FocusedSliceReport(
        full_history_tokens=full_tokens,
        avg_slice_tokens=avg_slice,
        avg_reduction_pct=avg_reduction,
        per_reference=per_ref,
    )


# ── scenario 4: cross-LLM consistency on the focused slice ─────────


_CROSS_LLM_SYSTEM = (
    "You are continuing a long-running conversation. Below is the "
    "relevant slice of prior context — the most-similar prior turns. "
    "Reply naturally to the user's latest message using only this "
    "slice. Keep your reply under 60 words. Output plain text only."
)


def _topic_keywords_for_reference(ref_text: str) -> list[str]:
    """Heuristic keyword set used to grade an LLM reply for topic accuracy."""
    lowered = ref_text.lower()
    if "email triage" in lowered:
        return ["email", "triage", "support", "auto-reply", "escalate"]
    if "hiring" in lowered or "devrel" in lowered:
        return ["devrel", "hiring", "frontend", "backend", "q3", "weeks", "notice"]
    if "lambda" in lowered:
        return ["lambda", "ecs", "cold-start", "control-plane", "cost"]
    return [w for w in lowered.split() if len(w) > 3][:3]


async def scenario_cross_llm(model: str) -> CrossLLMReport:
    """For each vague reference, retrieve focused slice, ask the LLM, grade output."""
    engine = RAGEngine()
    for idx, (speaker, text) in enumerate(CONVERSATION):
        await engine.store(_format_turn(idx, speaker, text))

    timeout = OLLAMA_REQUEST_TIMEOUT_S if _is_ollama(model) else PAID_REQUEST_TIMEOUT_S
    correct = 0
    spend = 0.0
    latencies: list[float] = []
    completed = 0
    failure: str | None = None

    for ref, _expected in VAGUE_REFERENCES:
        if not _is_ollama(model) and spend >= PER_MODEL_SPEND_CAP_USD:
            failure = (
                f"per-model spend cap hit after {completed} samples "
                f"(${spend:.4f} >= ${PER_MODEL_SPEND_CAP_USD:.2f})"
            )
            break
        slice_lines = await engine.retrieve(ref, top_k=3)
        slice_text = "\n".join(slice_lines)
        messages = [
            {"role": "system", "content": _CROSS_LLM_SYSTEM},
            {"role": "user", "content":
                f"Prior context (focused slice):\n{slice_text}\n\n"
                f"My latest message: {ref}"},
        ]
        t0 = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(
                    model=model,
                    messages=messages,
                    temperature=0.0,
                    max_tokens=120,
                ),
                timeout=timeout + 5.0,
            )
        except Exception as exc:
            latencies.append((time.perf_counter() - t0) * 1000.0)
            completed += 1
            # Don't grade as correct — the LLM call failed.
            if failure is None:
                failure = f"LLM call error on '{ref[:32]}…': {type(exc).__name__}"
            continue
        latency_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(latency_ms)
        completed += 1
        try:
            raw = (response.choices[0].message.content or "").lower()
        except (AttributeError, IndexError):
            raw = ""
        keywords = _topic_keywords_for_reference(ref)
        if any(k.lower() in raw for k in keywords):
            correct += 1
        spend += _safe_completion_cost(model, response)

    return CrossLLMReport(
        model=model,
        samples_attempted=len(VAGUE_REFERENCES),
        samples_completed=completed,
        answers_referenced_correct_topic=correct,
        p50_latency_ms=statistics.median(latencies) if latencies else 0.0,
        p99_latency_ms=_percentile(latencies, 99.0),
        avg_cost_usd=spend / completed if completed else 0.0,
        total_cost_usd=spend,
        failure_reason=failure,
    )


# ── scenario 5: cross-tenant leak count ────────────────────────────


async def scenario_cross_tenant() -> CrossTenantReport:
    """Two project-scoped VectorMemory instances; verify no cross-leak."""
    store_a = VectorMemory(project_id="tenant-a")
    store_b = VectorMemory(project_id="tenant-b")

    await store_a.store("Tenant A's secret note: alpha-12345")
    await store_a.store("Tenant A's runbook references service-A-prod and service-A-staging")
    await store_b.store("Tenant B's secret note: beta-67890")
    await store_b.store("Tenant B's runbook references service-B-prod and service-B-staging")

    a_self = await store_a.retrieve("alpha-12345", top_k=3)
    b_self = await store_b.retrieve("beta-67890", top_k=3)
    a_for_b = await store_a.retrieve("beta-67890", top_k=3)
    b_for_a = await store_b.retrieve("alpha-12345", top_k=3)

    a_isolated = all("beta-67890" not in r for r in a_for_b)
    b_isolated = all("alpha-12345" not in r for r in b_for_a)
    leak = sum(1 for r in a_for_b if "beta-67890" in r)
    leak += sum(1 for r in b_for_a if "alpha-12345" in r)

    return CrossTenantReport(
        tenant_a_self_retrievable=len(a_self) > 0,
        tenant_b_self_retrievable=len(b_self) > 0,
        a_isolated_from_b=a_isolated,
        b_isolated_from_a=b_isolated,
        cross_leak_count=leak,
    )


# ── scenario 6: hallucination rate against closed-domain corpus ────


_HALLUCINATION_SYSTEM = (
    "You are a documentation assistant. Answer ONLY using the facts "
    "below. If the answer is not contained in the facts, reply EXACTLY: "
    "\"I don't know based on the provided documentation.\" Do not "
    "speculate. Do not add information that is not in the facts."
)


def _refused(text: str) -> bool:
    norm = text.lower().strip()
    triggers = (
        "i don't know based on the provided documentation",
        "i do not know based on the provided documentation",
        "not in the provided documentation",
        "no information in the provided",
        "the documentation does not",
    )
    return any(t in norm for t in triggers)


def _grounded(text: str, fact_keywords: list[str]) -> bool:
    norm = text.lower()
    return any(kw.lower() in norm for kw in fact_keywords)


_GROUNDED_KEYWORDS: dict[str, list[str]] = {
    IN_SCOPE_QUESTIONS[0]: ["tf-idf", "cosine"],
    IN_SCOPE_QUESTIONS[1]: ["heartbeat", "30 second", "registry"],
    IN_SCOPE_QUESTIONS[2]: ["curator", "training sample", "agent run"],
    IN_SCOPE_QUESTIONS[3]: ["nn_short_name", "nn_", "examples"],
    IN_SCOPE_QUESTIONS[4]: ["agpl", "commercial"],
}


async def scenario_hallucination(model: str) -> HallucinationReport:
    """Probe in-scope and out-of-scope; measure refusal + grounded rate."""
    timeout = OLLAMA_REQUEST_TIMEOUT_S if _is_ollama(model) else PAID_REQUEST_TIMEOUT_S
    facts_block = "\n".join(f"- {f}" for f in HALLUCINATION_FACTS)
    spend = 0.0
    in_scope_grounded = 0
    out_refused = 0
    failure: str | None = None

    async def _ask(question: str) -> str | None:
        nonlocal spend, failure
        if not _is_ollama(model) and spend >= PER_MODEL_SPEND_CAP_USD:
            if failure is None:
                failure = (
                    f"per-model spend cap hit (${spend:.4f} >= "
                    f"${PER_MODEL_SPEND_CAP_USD:.2f})"
                )
            return None
        messages = [
            {"role": "system", "content":
                f"{_HALLUCINATION_SYSTEM}\n\nFacts:\n{facts_block}"},
            {"role": "user", "content": question},
        ]
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(
                    model=model,
                    messages=messages,
                    temperature=0.0,
                    max_tokens=120,
                ),
                timeout=timeout + 5.0,
            )
        except Exception as exc:
            if failure is None:
                failure = f"LLM call error: {type(exc).__name__}"
            return None
        try:
            text = (response.choices[0].message.content or "").strip()
        except (AttributeError, IndexError):
            text = ""
        spend += _safe_completion_cost(model, response)
        return text

    for q in IN_SCOPE_QUESTIONS:
        answer = await _ask(q)
        if answer is not None:
            keywords = _GROUNDED_KEYWORDS.get(q, [])
            if keywords and _grounded(answer, keywords):
                in_scope_grounded += 1

    for q in OUT_OF_SCOPE_QUESTIONS:
        answer = await _ask(q)
        if answer is not None and _refused(answer):
            out_refused += 1

    out_count = len(OUT_OF_SCOPE_QUESTIONS)
    refusal_rate = out_refused / max(out_count, 1)
    hallucination_rate = 1.0 - refusal_rate
    completed = len(IN_SCOPE_QUESTIONS) + len(OUT_OF_SCOPE_QUESTIONS)
    return HallucinationReport(
        model=model,
        in_scope_questions=len(IN_SCOPE_QUESTIONS),
        in_scope_grounded=in_scope_grounded,
        out_of_scope_questions=out_count,
        out_of_scope_refused=out_refused,
        hallucination_rate=hallucination_rate,
        avg_cost_usd=spend / completed if completed else 0.0,
        total_cost_usd=spend,
        failure_reason=failure,
    )


# ── orchestration ──────────────────────────────────────────────────


@dataclass
class SoakResults:
    scenario_1_corpus_retrieval: CorpusRetrievalReport
    scenario_2_embedder_comparison: EmbedderComparisonReport
    scenario_3_focused_slice: FocusedSliceReport
    scenario_4_cross_llm: list[CrossLLMReport]
    scenario_5_cross_tenant: CrossTenantReport
    scenario_6_hallucination: list[HallucinationReport]


async def run_soak() -> tuple[SoakResults, float]:
    """Run every scenario in order, halt on total spend cap. Return results + spend."""
    print("─── Scenario 1: long-corpus retrieval (TF-IDF) ".ljust(72, "─"))
    s1 = await scenario_corpus_retrieval()
    print(
        f"  recall@5 {s1.recall_at_5 * 100:5.1f}% on {s1.corpus_size} docs "
        f"× {s1.query_count} queries, avg {s1.avg_results_returned:.1f} results returned"
    )
    print()

    print("─── Scenario 2: TF-IDF vs SentenceTransformer ".ljust(72, "─"))
    s2 = await scenario_embedder_comparison()
    if not s2.available:
        print(f"  SKIP {s2.skip_reason}")
    else:
        delta = (s2.recall_at_5 or 0.0) - s1.recall_at_5
        sign = "+" if delta >= 0 else ""
        print(
            f"  ST recall@5 {(s2.recall_at_5 or 0) * 100:5.1f}% "
            f"(TF-IDF {s1.recall_at_5 * 100:5.1f}%, delta {sign}{delta * 100:.1f}pp)"
        )
    print()

    print("─── Scenario 3: focused-slice token reduction (Gap #5) ".ljust(72, "─"))
    s3 = await scenario_focused_slice()
    print(
        f"  full history {s3.full_history_tokens} tokens → "
        f"avg slice {s3.avg_slice_tokens} tokens "
        f"({s3.avg_reduction_pct:.0f}% reduction across "
        f"{len(s3.per_reference)} vague references)"
    )
    print()

    print("─── Scenario 5: cross-tenant leak count ".ljust(72, "─"))
    s5 = await scenario_cross_tenant()
    print(
        f"  cross-leak {s5.cross_leak_count} "
        f"(A↔A {s5.tenant_a_self_retrievable}, B↔B {s5.tenant_b_self_retrievable}, "
        f"A↛B {s5.a_isolated_from_b}, B↛A {s5.b_isolated_from_a})"
    )
    print()

    models = _selected_models()
    s4_reports: list[CrossLLMReport] = []
    s6_reports: list[HallucinationReport] = []
    total_spend = 0.0

    if not models:
        print("─── Scenarios 4 + 6: cross-LLM + hallucination ".ljust(72, "─"))
        print(
            "  SKIP no LLMs configured.\n"
            "    Set ANTHROPIC_API_KEY / OPENAI_API_KEY in ~/.sagewai/.env,\n"
            "    or run 'ollama serve' with a chat-tuned model pulled\n"
            "    (e.g. 'ollama pull llama3.2'), then re-run."
        )
        print()
    else:
        print("─── Scenario 4: cross-LLM consistency ".ljust(72, "─"))
        print("  Models in rotation:")
        for m in models:
            print(f"    - {m}")
        print()
        for model in models:
            print(f"  · {model}")
            r = await scenario_cross_llm(model)
            s4_reports.append(r)
            total_spend += r.total_cost_usd
            if r.failure_reason:
                print(f"    halted: {r.failure_reason}")
            print(
                f"    answers-on-topic {r.answers_referenced_correct_topic}"
                f"/{r.samples_attempted} | p50 {r.p50_latency_ms:>6.0f}ms "
                f"| p99 {r.p99_latency_ms:>6.0f}ms | total ${r.total_cost_usd:.6f}"
            )
            if total_spend >= TOTAL_SPEND_CAP_USD:
                print(f"  ! total cap hit (${total_spend:.4f}); skipping remaining")
                break
        print()

        if total_spend < TOTAL_SPEND_CAP_USD:
            print("─── Scenario 6: hallucination rate ".ljust(72, "─"))
            for model in models:
                if total_spend >= TOTAL_SPEND_CAP_USD:
                    print(f"  ! total cap hit; skipping {model}")
                    break
                print(f"  · {model}")
                h = await scenario_hallucination(model)
                s6_reports.append(h)
                total_spend += h.total_cost_usd
                if h.failure_reason:
                    print(f"    halted: {h.failure_reason}")
                print(
                    f"    in-scope grounded {h.in_scope_grounded}/{h.in_scope_questions} "
                    f"| out-of-scope refused {h.out_of_scope_refused}/{h.out_of_scope_questions} "
                    f"| hallucination {h.hallucination_rate * 100:.0f}% "
                    f"| total ${h.total_cost_usd:.6f}"
                )
            print()

    results = SoakResults(
        scenario_1_corpus_retrieval=s1,
        scenario_2_embedder_comparison=s2,
        scenario_3_focused_slice=s3,
        scenario_4_cross_llm=s4_reports,
        scenario_5_cross_tenant=s5,
        scenario_6_hallucination=s6_reports,
    )
    return results, total_spend


def _print_proof(results: SoakResults, total_spend: float) -> None:
    s1 = results.scenario_1_corpus_retrieval
    s2 = results.scenario_2_embedder_comparison
    s3 = results.scenario_3_focused_slice
    s5 = results.scenario_5_cross_tenant

    print("─── The proof ".ljust(72, "─"))
    print()
    print(f"  recall@5 (TF-IDF, 100 docs × 50 queries) ............ {s1.recall_at_5 * 100:5.1f}%  {_bar(s1.recall_at_5)}")
    if s2.available and s2.recall_at_5 is not None:
        print(f"  recall@5 (SentenceTransformer, same dataset) ......... {s2.recall_at_5 * 100:5.1f}%  {_bar(s2.recall_at_5)}")
    else:
        print(f"  recall@5 (SentenceTransformer) ....................... SKIP  ({s2.skip_reason})")
    print(f"  focused-slice reduction (Gap #5, 14-turn convo) ...... {s3.avg_reduction_pct:5.1f}%  {_bar(s3.avg_reduction_pct / 100)}")
    print(f"  full history → slice tokens .......................... {s3.full_history_tokens} → {s3.avg_slice_tokens}")
    print(f"  cross-tenant leak count (project-scoped stores) ...... {s5.cross_leak_count}")
    if results.scenario_4_cross_llm:
        print()
        print("  cross-LLM (focused slice → reply on-topic):")
        print(f"    {'model':<42s}  on-topic   p50ms   p99ms   $/call    total$")
        print(f"    {'-' * 42}  --------  ------  ------  --------  --------")
        for r in results.scenario_4_cross_llm:
            print(
                f"    {r.model:<42s}  {r.answers_referenced_correct_topic}"
                f"/{r.samples_attempted}      "
                f"{r.p50_latency_ms:>6.0f}  {r.p99_latency_ms:>6.0f}  "
                f"{r.avg_cost_usd:>8.6f}  {r.total_cost_usd:>8.4f}"
            )
    if results.scenario_6_hallucination:
        print()
        print("  hallucination rate (lower is better):")
        print(f"    {'model':<42s}  in-scope  refused   halluc%   total$")
        print(f"    {'-' * 42}  --------  --------  --------  --------")
        for h in results.scenario_6_hallucination:
            print(
                f"    {h.model:<42s}  "
                f"{h.in_scope_grounded}/{h.in_scope_questions}       "
                f"{h.out_of_scope_refused}/{h.out_of_scope_questions}       "
                f"{h.hallucination_rate * 100:>6.1f}    "
                f"{h.total_cost_usd:>8.4f}"
            )
    print()
    print(f"  Total spend across LLM scenarios: ${total_spend:.4f} "
          f"(cap was ${TOTAL_SPEND_CAP_USD:.2f})")
    print()


def _write_results(results: SoakResults, total_spend: float) -> Path:
    out_path = Path(
        os.environ.get("SAGEWAI_SOAK_RESULTS_PATH")
        or (Path.home() / ".sagewai" / "memory-soak-results.json")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "soak": "memory",
        "issue": "atelier#9",
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "per_model_spend_cap_usd": PER_MODEL_SPEND_CAP_USD,
        "total_spend_cap_usd": TOTAL_SPEND_CAP_USD,
        "total_spend_usd": total_spend,
        "scenarios": {
            "1_corpus_retrieval_tfidf": asdict(results.scenario_1_corpus_retrieval),
            "2_embedder_comparison": asdict(results.scenario_2_embedder_comparison),
            "3_focused_slice_gap5": asdict(results.scenario_3_focused_slice),
            "4_cross_llm": [asdict(r) for r in results.scenario_4_cross_llm],
            "5_cross_tenant": asdict(results.scenario_5_cross_tenant),
            "6_hallucination": [asdict(r) for r in results.scenario_6_hallucination],
        },
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    return out_path


# ── entry point ────────────────────────────────────────────────────


async def main() -> None:
    print("─" * 72)
    print(" Sagewai — memory + RAG soak (atelier issue #9, soak A)")
    print("─" * 72)
    print()
    print(
        "  Six scenarios. Three run with no credentials (TF-IDF recall, "
        "focused-slice\n  token reduction, cross-tenant isolation). Three more "
        "engage when LLM keys\n  are present in ~/.sagewai/.env (cross-LLM "
        "consistency, hallucination rate)\n  or when sentence-transformers is "
        "installed (TF-IDF vs ST)."
    )
    print()
    print(f"  Per-model spend cap: ${PER_MODEL_SPEND_CAP_USD:.2f} | Total cap: ${TOTAL_SPEND_CAP_USD:.2f}")
    print()

    results, total_spend = await run_soak()
    _print_proof(results, total_spend)
    out_path = _write_results(results, total_spend)
    print(f"  Raw results: {out_path}")
    print()
    print("  Next step: paste the per-scenario numbers into")
    print("  sagewai/atelier:docs/v1.0/memory-soak-report.md (template lives")
    print("  in this script's sibling README).")


if __name__ == "__main__":
    asyncio.run(main())
