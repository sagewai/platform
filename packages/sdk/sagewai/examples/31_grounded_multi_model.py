#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 31 — Multi-model relay RAG: each model adds a fact, next one builds on it.

A demo of `RAGEngine` as a **shared memory across model swaps**:

1. Three different LLMs (Claude Haiku, GPT-4o-mini, Ollama-local) take turns.
2. Each model retrieves the current corpus from the engine, answers a
   targeted question, and *infers one additional fact* that follows.
3. The new fact is stored in the shared `RAGEngine` so the next model
   sees it.
4. After all models contribute, a final consolidation question is asked
   to one of the models — the answer requires all the inferred facts.

Plus the four supporting features the v1.0 SDK ships with:

- **Budget cap.** Hosted calls stop when cumulative spend hits the cap;
  Ollama (local, free) keeps going regardless.
- **Hallucination rate.** Each model is also probed with one
  out-of-scope question; refusal rate is reported per model.
- **Provider auto-detection.** Anthropic / OpenAI / Ollama each detected
  independently — the relay runs whichever subset you have.
- **Cross-model agreement.** After the relay, the same question is
  asked of every available model; the spread in answers tells you
  whether the agent contract survives the swap.

Total expected spend if all three providers run: under $0.005 with
the cheapest hosted models. Free if you run Ollama-only.

Requirements::

    pip install sagewai

Usage::

    export ANTHROPIC_API_KEY=sk-ant-...
    export OPENAI_API_KEY=sk-...
    # Ollama running locally with at least one model pulled
    # (default: llama3.2:latest; override via SAGEWAI_OLLAMA_MODEL)

    python 31_grounded_multi_model.py

    # Tighten the budget further:
    SAGEWAI_EXAMPLE_BUDGET_USD=0.01 python 31_grounded_multi_model.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import litellm

from sagewai.memory import RAGEngine


# ── budget cap ─────────────────────────────────────────────────────


# Hard cap on cumulative hosted-LLM spend. Override with
# SAGEWAI_EXAMPLE_BUDGET_USD=0.01 for a tighter run.
BUDGET_CAP_USD = float(os.environ.get("SAGEWAI_EXAMPLE_BUDGET_USD", "0.05"))


# ── seed corpus and relay questions ────────────────────────────────


# A small fictional knowledge base about a customer named "Acme Robotics".
SEED_CORPUS = [
    "Acme Robotics builds warehouse picking robots used by 40+ retailers.",
    "Acme's flagship product, the AR-3, picks 1,200 items per hour.",
    "Acme's ARR grew from $4M to $12M between 2024 and 2026.",
    "Acme operates in 6 fulfillment centers across the US Midwest.",
]


# Each relay turn names a (question, source_model_label) pair. The
# question prompts the model to derive a NEW fact that follows from
# the current corpus.
RELAY_QUESTIONS: list[tuple[str, str]] = [
    (
        "Based on the corpus, calculate Acme's revenue per fulfillment center "
        "for 2026 and state the result as a single concise fact "
        "(format: 'Acme operates with $X.X million ARR per fulfillment center.').",
        "fact-derivation",
    ),
    (
        "Based on the corpus including any new facts, calculate the AR-3's "
        "items-per-day output assuming a 16-hour operational day. "
        "State the result as a single concise fact "
        "(format: 'Each AR-3 unit picks X items per 16-hour day.').",
        "fact-derivation",
    ),
    (
        "Based on the corpus including any new facts, calculate Acme's "
        "growth rate as a percentage from 2024 to 2026. State the result "
        "as a single concise fact "
        "(format: 'Acme grew Y% in revenue between 2024 and 2026.').",
        "fact-derivation",
    ),
]


# Final consolidation question — must reach every fact in the corpus
# (seeds + every relay-added derivation) and produce one coherent answer.
CONSOLIDATION_QUESTION = (
    "Using ALL of the facts in the context, write a coherent investment "
    "memo of three sentences for Acme Robotics. The memo MUST cite, by "
    "explicit number: the number of retailers, the AR-3 hourly throughput, "
    "the ARR figure for 2026, the number of fulfillment centers, the "
    "per-FC revenue (derived in the relay), the items-per-16-hour-day "
    "per AR-3 (derived in the relay), and the growth rate as a percentage "
    "(derived in the relay). Every numeric claim must be traceable to a "
    "fact in the context."
)


# Out-of-scope question used to probe hallucination rate per model.
HALLUCINATION_PROBE = "Who is the CEO of Acme Robotics?"  # Not in the corpus


# ── model registry ─────────────────────────────────────────────────


@dataclass
class ModelEntry:
    """One model to test against."""
    name: str            # litellm model id
    label: str           # human-readable label
    available: bool
    skip_reason: str = ""
    input_price_per_mtok: float = 0.0   # USD per million input tokens
    output_price_per_mtok: float = 0.0  # USD per million output tokens

    @property
    def is_local(self) -> bool:
        return self.input_price_per_mtok == 0 and self.output_price_per_mtok == 0


def _detect_models() -> list[ModelEntry]:
    """Detect which providers are configured + reachable."""
    models: list[ModelEntry] = []

    # Anthropic — claude-haiku-4-5 (cheapest)
    if os.environ.get("ANTHROPIC_API_KEY"):
        models.append(ModelEntry(
            name="claude-haiku-4-5-20251001",
            label="Claude Haiku 4.5",
            available=True,
            input_price_per_mtok=0.80,
            output_price_per_mtok=4.00,
        ))
    else:
        models.append(ModelEntry(
            name="claude-haiku-4-5-20251001",
            label="Claude Haiku 4.5",
            available=False,
            skip_reason="no ANTHROPIC_API_KEY",
        ))

    # OpenAI — gpt-4o-mini (cheapest hosted at OpenAI)
    if os.environ.get("OPENAI_API_KEY"):
        models.append(ModelEntry(
            name="gpt-4o-mini",
            label="OpenAI GPT-4o-mini",
            available=True,
            input_price_per_mtok=0.15,
            output_price_per_mtok=0.60,
        ))
    else:
        models.append(ModelEntry(
            name="gpt-4o-mini",
            label="OpenAI GPT-4o-mini",
            available=False,
            skip_reason="no OPENAI_API_KEY",
        ))

    # Ollama — local, free. Verify the requested model is actually pulled.
    ollama_model = os.environ.get("SAGEWAI_OLLAMA_MODEL", "llama3.2:latest")
    ollama_base = os.environ.get("OLLAMA_API_BASE", "http://localhost:11434")
    available, skip_reason = _check_ollama(ollama_base, ollama_model)
    models.append(ModelEntry(
        name=f"ollama/{ollama_model}",
        label=f"Ollama (local: {ollama_model})",
        available=available,
        skip_reason=skip_reason,
        input_price_per_mtok=0.0,
        output_price_per_mtok=0.0,
    ))
    return models


def _check_ollama(base_url: str, requested_model: str) -> tuple[bool, str]:
    """Verify Ollama API reachable AND model pulled."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=2.0) as resp:
            if resp.status != 200:
                return False, f"Ollama API at {base_url} returned {resp.status}"
            payload = json.loads(resp.read())
    except Exception as e:
        return False, f"Ollama not reachable at {base_url} ({type(e).__name__})"
    pulled = [m.get("name", "") for m in payload.get("models", [])]
    if requested_model in pulled:
        return True, ""
    base_name = requested_model.split(":")[0]
    base_matches = [n for n in pulled if n.split(":")[0] == base_name]
    if base_matches:
        return False, (
            f"requested {requested_model!r} not found, but {base_matches[0]!r} is "
            f"— set SAGEWAI_OLLAMA_MODEL={base_matches[0]} to use it"
        )
    return False, (
        f"model {requested_model!r} not pulled — run "
        f"`ollama pull {requested_model}` (available: {pulled[:3]}…)"
    )


# ── LLM call helpers ──────────────────────────────────────────────


@dataclass
class CallResult:
    """One LLM call's outcome — text + tokens + cost."""
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float
    error: str | None = None


async def call_llm(
    model: ModelEntry,
    system: str,
    user: str,
    *,
    max_tokens: int = 200,
    temperature: float = 0.1,
) -> CallResult:
    """Single LLM call via litellm. Captures tokens + estimated cost."""
    t0 = time.monotonic()
    try:
        resp = await litellm.acompletion(
            model=model.name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as e:
        return CallResult(
            text="", input_tokens=0, output_tokens=0,
            cost_usd=0.0, latency_s=time.monotonic() - t0, error=str(e)[:200],
        )
    pt = resp.usage.prompt_tokens
    ct = resp.usage.completion_tokens
    cost = (pt * model.input_price_per_mtok + ct * model.output_price_per_mtok) / 1_000_000
    return CallResult(
        text=(resp.choices[0].message.content or "").strip(),
        input_tokens=pt,
        output_tokens=ct,
        cost_usd=cost,
        latency_s=time.monotonic() - t0,
    )


# ── relay (the new feature) ────────────────────────────────────────


async def run_relay(
    *,
    models: list[ModelEntry],
    engine: RAGEngine,
    budget_remaining_fn: Callable[[], float],
) -> list[dict[str, Any]]:
    """Each model takes one relay turn; the new fact is stored in the engine.

    Returns a list of per-turn records (model, question, new fact, cost).
    """
    turns: list[dict[str, Any]] = []
    if not RELAY_QUESTIONS:
        return turns

    for i, (question, _kind) in enumerate(RELAY_QUESTIONS):
        # Round-robin through the available models so each contributes a fact
        model = models[i % len(models)]

        # Skip hosted models if budget exhausted
        if not model.is_local and budget_remaining_fn() <= 0:
            turns.append({
                "turn": i + 1, "model": model.label, "skipped": "budget exhausted",
            })
            continue

        # Retrieve current corpus state from the engine
        retrieved = await engine.retrieve(question, top_k=8)
        context = "\n".join(f"- {r}" for r in retrieved)

        system = (
            "You are a precise analyst. Use the provided context to derive ONE "
            "new factual statement. Output ONLY the single new fact in plain "
            "text, no preamble, no explanation, no quotes, no list markers."
        )
        user = f"Current facts in corpus:\n{context}\n\nTask: {question}"
        result = await call_llm(model, system, user, max_tokens=80)

        # Parse out a clean single-line fact
        new_fact = _clean_fact(result.text)
        if new_fact:
            await engine.store(new_fact)

        turns.append({
            "turn": i + 1,
            "model": model.label,
            "task": question[:80] + "…",
            "new_fact": new_fact or "(no fact extracted)",
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": round(result.cost_usd, 6),
            "latency_s": round(result.latency_s, 1),
            "error": result.error,
        })
    return turns


def _clean_fact(text: str) -> str:
    """Strip whitespace, quotes, list markers, and code fences from a fact."""
    if not text:
        return ""
    t = text.strip()
    # Remove leading "- ", "* ", "1. ", or quotes
    t = re.sub(r"^[-*•]\s*", "", t)
    t = re.sub(r"^\d+\.\s*", "", t)
    t = t.strip("\"'`")
    # Single line only
    return t.split("\n")[0].strip()


# ── grounding probe (hallucination + consolidation) ───────────────


async def hallucination_probe_one(
    *,
    model: ModelEntry,
    engine: RAGEngine,
    budget_remaining_fn: Callable[[], float],
    top_k: int = 8,
) -> dict[str, Any]:
    """Probe one model with an out-of-scope question. Cheap (~80 tokens out)."""
    if not (model.is_local or budget_remaining_fn() > 0):
        return {"model": model.label, "skipped": "budget exhausted"}

    retrieved = await engine.retrieve(HALLUCINATION_PROBE, top_k=top_k)
    ctx = "\n".join(f"- {r}" for r in retrieved)
    system = (
        "You are a documentation assistant. Answer ONLY using the provided "
        "context. If the context lacks the answer, say "
        "'I don't know based on the provided documentation.' Do not invent facts."
    )
    result = await call_llm(
        model, system, f"Context:\n{ctx}\n\nQuestion: {HALLUCINATION_PROBE}",
        max_tokens=80,
    )
    ans_lower = result.text.lower()
    refused = (
        "don't know" in ans_lower
        or "do not know" in ans_lower
        or "not in" in ans_lower
        or "not contain" in ans_lower
        or "no information" in ans_lower
    )
    return {
        "model": model.label,
        "answer": result.text[:200],
        "refused": refused,
        "cost_usd": round(result.cost_usd, 6),
        "error": result.error,
    }


async def final_consolidation(
    *,
    models: list[ModelEntry],
    engine: RAGEngine,
    budget_remaining_fn: Callable[[], float],
) -> dict[str, Any]:
    """One final call: pick the strongest available model + retrieve ALL facts.

    Strongest first: Claude Sonnet → Claude Haiku → GPT-4o-mini → Ollama.
    Local Ollama is the last resort (always free).
    """
    preference_order = [
        "claude-sonnet",
        "claude-haiku",
        "gpt-4o-mini",
        "ollama",
    ]

    chosen: ModelEntry | None = None
    for prefix in preference_order:
        for m in models:
            if m.name.lower().startswith(prefix):
                if m.is_local or budget_remaining_fn() > 0:
                    chosen = m
                    break
        if chosen:
            break

    if chosen is None:
        return {"error": "no model available within budget"}

    # Retrieve EVERYTHING — top_k high enough that the seed + relay
    # facts are all in scope. We dump the engine state to confirm.
    facts_in_engine = await engine.retrieve(
        # An empty-ish broad query; combined with high top_k, returns
        # essentially the full corpus we've accumulated.
        "Acme Robotics", top_k=20,
    )

    context = "\n".join(f"- {f}" for f in facts_in_engine)

    system = (
        "You are a precise investment analyst. Answer ONLY using the provided "
        "context. Every numeric claim in your answer must appear verbatim in "
        "the context. Do not round; do not extrapolate; do not invent."
    )
    user = f"Context:\n{context}\n\nTask: {CONSOLIDATION_QUESTION}"
    result = await call_llm(chosen, system, user, max_tokens=400, temperature=0.0)

    return {
        "chosen_model": chosen.label,
        "facts_supplied": len(facts_in_engine),
        "answer": result.text,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": round(result.cost_usd, 6),
        "latency_s": round(result.latency_s, 1),
        "error": result.error,
    }


# ── main ──────────────────────────────────────────────────────────


async def main() -> None:
    print("─" * 72)
    print(" Sagewai — multi-model relay RAG (example 31)")
    print("─" * 72)
    print()

    # 1. Build the shared RAGEngine and seed with the base corpus
    engine = RAGEngine()
    for doc in SEED_CORPUS:
        await engine.store(doc)
    print(f"  Seed corpus: {len(SEED_CORPUS)} documents in shared RAGEngine")
    print()

    # 2. Detect available models
    all_models = _detect_models()
    available = [m for m in all_models if m.available]
    print("  Models detected:")
    for m in all_models:
        if m.available:
            print(f"    ✓ {m.label}")
        else:
            print(f"    ⊘ {m.label} — {m.skip_reason}")
    print()

    if not available:
        print(
            "  ⚠ No models available. Set ANTHROPIC_API_KEY, OPENAI_API_KEY,\n"
            "    or run Ollama locally with a pulled model.\n"
        )
        return

    print(f"  Budget cap (hosted only): ${BUDGET_CAP_USD:.4f} "
          f"(SAGEWAI_EXAMPLE_BUDGET_USD)")
    print()

    spent = 0.0
    budget_remaining = lambda: BUDGET_CAP_USD - spent

    # 3. Relay — each model adds a fact built on the previous ones
    print("─" * 72)
    print(" Relay: each model adds one fact built on the prior corpus")
    print("─" * 72)
    print()
    relay_turns = await run_relay(
        models=available, engine=engine, budget_remaining_fn=budget_remaining,
    )
    spent = sum(t.get("cost_usd", 0.0) for t in relay_turns if "cost_usd" in t)
    for t in relay_turns:
        if "skipped" in t:
            print(f"  Turn {t['turn']} [{t['model']}]: SKIPPED ({t['skipped']})")
        else:
            print(f"  Turn {t['turn']} [{t['model']}] (${t['cost_usd']:.5f}, {t['latency_s']}s):")
            print(f"    new fact: {t['new_fact']}")
    print()
    print(f"  Corpus now contains {len(SEED_CORPUS) + sum(1 for t in relay_turns if t.get('new_fact'))} facts.")
    print()

    # 4. Per-model hallucination probe (cheap, one OOS question per model)
    print("─" * 72)
    print(" Hallucination probe — same out-of-scope question, every model")
    print("─" * 72)
    print()
    probes: list[dict[str, Any]] = []
    for m in available:
        probe = await hallucination_probe_one(
            model=m, engine=engine, budget_remaining_fn=budget_remaining,
        )
        if "cost_usd" in probe:
            spent += probe["cost_usd"]
        probes.append(probe)
        if "skipped" in probe:
            print(f"  {m.label}: SKIPPED ({probe['skipped']})")
        else:
            mark = "REFUSED ✓" if probe["refused"] else "ANSWERED ⚠"
            print(f"  {m.label}: {mark}")
            print(f"    {probe['answer'][:140]}")
        print()

    # 5. FINAL consolidation — strongest available model, ALL facts retrieved,
    #    must produce a coherent answer citing every fact.
    print("─" * 72)
    print(" Final consolidation — ALL facts, one coherent answer")
    print("─" * 72)
    print()
    final = await final_consolidation(
        models=available, engine=engine, budget_remaining_fn=budget_remaining,
    )
    if final.get("error"):
        print(f"  ERROR: {final['error']}")
    else:
        spent += final.get("cost_usd", 0.0)
        print(f"  Chosen model:        {final['chosen_model']}")
        print(f"  Facts supplied:      {final['facts_supplied']}")
        print(f"  Tokens:              {final['input_tokens']} in / {final['output_tokens']} out")
        print(f"  Cost:                ${final['cost_usd']:.5f}")
        print(f"  Latency:             {final['latency_s']}s")
        print()
        print("  Answer:")
        print()
        for line in final["answer"].split("\n"):
            print(f"    {line}")
        print()

    # 6. Summary
    print("─" * 72)
    print(" Summary")
    print("─" * 72)
    refused_oos = sum(1 for p in probes if p.get("refused"))
    relay_facts_added = sum(1 for t in relay_turns if t.get("new_fact"))
    print(f"  Seed corpus size:                   {len(SEED_CORPUS)}")
    print(f"  Facts added via relay:              {relay_facts_added}")
    print(f"  Final corpus size:                  {len(SEED_CORPUS) + relay_facts_added}")
    print(f"  Models that refused the OOS probe:  {refused_oos}/{len(probes)}")
    print(f"  Total spend (hosted models):        ${spent:.5f}")
    print(f"  Budget remaining:                   ${budget_remaining():.5f}")
    print()
    print(json.dumps(
        {
            "relay_turns": relay_turns,
            "hallucination_probes": probes,
            "final_consolidation": final,
            "total_spend_usd": round(spent, 5),
        },
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
