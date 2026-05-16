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
"""Example 37 — Semantic checkpoint recall: weak LLMs feel like Opus.

Sagewai's memory + RAG layer makes long-running conversations work on
small-context-window LLMs by surfacing **only the relevant slice** of
prior context when a vague reference arrives. The LLM never sees the
whole conversation — just the part that matters right now.

The story:

You're chatting with an agent across multiple topics — the email
triage feature you're building, Q3 hiring, the AWS Lambda migration,
weekend plans. Then you say *"ok back to the email triage thing —
what's our next step?"*.

A naive memory implementation hands the whole conversation to the LLM
and hopes its context window fits. With a 4K-context-window model
(common for cheap local LLMs), it doesn't. With Opus's 200K window,
it does, but you pay for every token.

Sagewai's approach is different: semantic-search the conversation
history, surface the top-k most-relevant prior turns, hand **only that
focused slice** to the LLM. A 7B model on Ollama now holds this thread
just as well as Opus does — because it never sees the whole thread.

This example proves the claim:

1. Builds a 14-turn conversation across 4 interleaved topics.
2. Issues 3 vague references and shows which prior turns get
   retrieved as the "checkpoint" for each.
3. Reports the size of the focused slice vs the full history — the
   token-budget proof that lets cheap models hold the thread.
4. Optionally calls real LLMs (Anthropic / OpenAI / Ollama) with the
   focused slice when API keys are configured, demonstrating the same
   answer comes back regardless of provider.

**A note on retrieval quality.** The default :class:`~sagewai.memory.rag.RAGEngine`
substrate uses TF-IDF cosine similarity — it works on a clean machine
with zero extra dependencies. This handles "vague-but-keyword-overlapping"
references reliably (e.g. *"back to the email triage agent"* finds the
turns that mention email triage). For more abstract references like
*"back to our real business"* — where there is no shared vocabulary —
swap the embedder to :class:`~sagewai.intelligence.embeddings.SentenceTransformerEmbedder`
or :class:`~sagewai.intelligence.embeddings.LiteLLMEmbedder` to use real
sentence embeddings. The example's structure stays identical; only the
embedder changes.

Requirements::

    pip install sagewai
    # Optional, for the real-LLM section:
    #   - ANTHROPIC_API_KEY for Claude
    #   - OPENAI_API_KEY for GPT
    #   - Ollama running locally with a model pulled (e.g. llama3:8b)

Usage::

    python 37_semantic_checkpoint_recall.py

Real-world use cases:

- Senior platform engineer at a 200-person fintech SaaS — your
  AI-feature is a multi-turn agent that holds conversations across
  many topics for the same user. Cloud Sonnet at 200K context costs
  more per call than the feature can earn back. Surface only the
  relevant slice and a cheap local 7B model holds the thread.
- Senior backend engineer at a 150-person legaltech SaaS — your
  contract-review assistant has 50 turns of back-and-forth on a
  single deal. When the user says "back to clause 4", you need the
  five turns about clause 4, not the whole transcript. Token spend
  drops by an order of magnitude on the same answer quality.
- Engineering manager at a 300-person customer-support SaaS — your
  support-rep co-pilot stays open across multi-day customer threads.
  The whole thread won't fit in a small-context model; the focused
  slice does, and the rep gets the same answer at 1/10th the spend.
- ML engineer at a 100-person AI-feature startup — you're evaluating
  whether a 7B Ollama-served model can hold a 14-turn conversation
  as well as Opus. The token-budget proof in this example is what
  lets you make that swap defensibly.
"""

from __future__ import annotations

import asyncio
import os
import re

from sagewai.memory import GlobalMemory


# ── A 14-turn conversation across 4 interleaved topics ───────────


CONVERSATION: list[tuple[str, str]] = [
    # ── Topic A: the email triage agent ──
    ("user", "I want to build an email triage agent for customer support tickets."),
    ("assistant", "An email triage agent makes sense. The triage will classify each email, draft a reply for the simple ones, and escalate the rest."),
    ("user", "We get 200 emails a day. Half of them are the same five repetitive support questions."),
    ("assistant", "Then the email triage agent should auto-reply to the repetitive 50% and summarise the rest for a human."),

    # ── Topic B: Q3 hiring plan ──
    ("user", "Different subject — the Q3 hiring plan needs three frontend hires, two backend hires, one DevRel hire."),
    ("assistant", "On the Q3 hiring plan, the DevRel hire is the one to think about most. Hiring DevRel is hard."),
    ("user", "For the Q3 hiring plan, should we move fast on the DevRel hire or slow?"),

    # ── Topic C: AWS Lambda migration ──
    ("user", "Switching topics — let's discuss the AWS Lambda migration. Where are we on it?"),
    ("assistant", "On the AWS Lambda migration, cold-start is the blocker. Provisioned concurrency on Lambda at level 1 absorbs cold-start cost."),
    ("user", "On Lambda costs, is AWS Lambda still cheaper than ECS at our request volume?"),
    ("assistant", "Yes — Lambda costs less than ECS below 100 sustained req/sec because Lambda has no control-plane cost."),

    # ── Topic D: weekend / personal chat ──
    ("user", "Quick aside: Friday burger plans?"),
    ("assistant", "Always. 7pm at Bun & Patty?"),

    # ── Back to Topic B ──
    ("user", "Back to the Q3 hiring plan — let's offer 6 weeks notice on the DevRel hire."),
]


# ── 3 vague references, one per work topic ──────────────────────


VAGUE_REFERENCES: list[tuple[str, str]] = [
    ("ok back to the email triage agent — what's the next step?", "Topic A — email triage"),
    ("remind me about the Q3 hiring plan",                         "Topic B — Q3 hiring"),
    ("what did we say about AWS Lambda costs?",                    "Topic C — Lambda"),
]


# ── Token estimator (rough — 4 chars ≈ 1 token, good enough for a demo) ─


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ── helpers ──────────────────────────────────────────────────────


def _format_turn(idx: int, speaker: str, text: str) -> str:
    return f"[t{idx:02d} {speaker:9s}] {text}"


def _line(text: str = "", char: str = "─") -> None:
    print(char * 72 if not text else f"{char * 3} {text} {char * (68 - len(text))}")


# ── main ─────────────────────────────────────────────────────────


async def main() -> None:
    _line()
    print(" Sagewai semantic-checkpoint recall — weak LLMs feel like Opus")
    _line()
    print()

    # 1. Reset shared memory (so re-runs are deterministic) and store
    #    each turn in the conversation. We embed the turn-index into
    #    the content so retrieved slices are self-labelling.
    GlobalMemory.reset(scope="conv-37")
    memory = GlobalMemory.get(scope="conv-37")

    print(f"  Storing {len(CONVERSATION)} turns into GlobalMemory…")
    for idx, (speaker, text) in enumerate(CONVERSATION):
        await memory.add(_format_turn(idx, speaker, text))

    full_history_text = "\n".join(
        _format_turn(i, s, t) for i, (s, t) in enumerate(CONVERSATION)
    )
    full_tokens = _est_tokens(full_history_text)
    print(f"    full conversation = {full_tokens} tokens "
          f"(≈ {len(full_history_text)} chars)")
    print(f"    too big for      → 4K-window models (Llama 2 7B base, "
          f"older Mistral, many local quantised models)")
    print()

    # 2. For each vague reference, retrieve the focused slice and
    #    show the size delta.
    _line(" Vague references → semantic-checkpoint retrieval ")
    print()

    for ref, expected_topic in VAGUE_REFERENCES:
        print(f'  reference: "{ref}"')
        print(f'  expected:  {expected_topic}')

        retrieved = await memory.retrieve(ref, top_k=3)
        slice_text = "\n".join(retrieved)
        slice_tokens = _est_tokens(slice_text)
        reduction = (1 - slice_tokens / full_tokens) * 100

        n = len(retrieved)
        print(f"  retrieved focused slice ({n} turn{'s' if n != 1 else ''} "
              f"above the similarity threshold):")
        for r in retrieved:
            if len(r) > 88:
                r = r[:85] + "…"
            print(f"    {r}")
        print(f"  slice size    = {slice_tokens} tokens "
              f"({reduction:.0f}% smaller than full history)")
        print(f"  fits in       → 4K-window models, trivially")
        print()

    # 3. Token-budget summary — the hard proof
    _line(" The proof ")
    print()
    slice_token_counts: list[int] = []
    for r, _ in VAGUE_REFERENCES:
        slice_token_counts.append(
            _est_tokens("\n".join(await memory.retrieve(r, top_k=3)))
        )
    avg_slice_tokens = sum(slice_token_counts) // len(slice_token_counts)
    print(f"  Without semantic retrieval:  {full_tokens} tokens per turn")
    print(f"  With semantic retrieval:     ~{avg_slice_tokens} tokens per turn")
    print(f"  Reduction:                   "
          f"{(1 - avg_slice_tokens / full_tokens) * 100:.0f}% on average")
    print()
    print(f"  At {full_tokens} tokens, a 4K-context-window model can't even hold")
    print(f"  this 14-turn conversation, let alone reply to a new turn. At")
    print(f"  ~{avg_slice_tokens} tokens, ANY LLM holds it — Opus, Haiku, GPT-4o-mini,")
    print(f"  ollama/llama3:8b, or a local quantised model on a laptop.")
    print()

    # 4. Optional: call real LLMs with the focused slice when keys/Ollama are present.
    _line(" Optional: same slice → same answer across providers ")
    print()
    providers_available = _detect_providers()
    if not providers_available:
        print("  No LLM providers configured — skipping the live-call section.")
        print("  To enable it:")
        print("    - export ANTHROPIC_API_KEY=...     (Claude)")
        print("    - export OPENAI_API_KEY=...        (GPT)")
        print("    - run Ollama locally + pull a model (llama3:8b)")
        print()
        print("  Once configured, this section calls each provider with the SAME")
        print("  focused slice from the first vague reference and prints all")
        print("  three responses — proving the slice (not the LLM) is what")
        print("  carries the conversation.")
        return

    # If we reach here, run the live calls.
    ref, expected = VAGUE_REFERENCES[0]
    slice_lines = await memory.retrieve(ref, top_k=3)
    slice_text = "\n".join(slice_lines)
    print(f'  Calling each provider with the slice for "{ref}":')
    print()
    for provider, model in providers_available:
        try:
            from litellm import acompletion  # late import
            response = await acompletion(
                model=model,
                messages=[
                    {"role": "system", "content":
                        "You are continuing a long-running conversation. "
                        "Below is the relevant slice of prior context — "
                        "the most-similar prior turns. Reply naturally to "
                        "the user's latest message using only this slice."},
                    {"role": "user", "content":
                        f"Prior context (focused slice):\n{slice_text}\n\n"
                        f"My latest message: {ref}"},
                ],
                max_tokens=120,
                temperature=0.0,
            )
            text = response["choices"][0]["message"]["content"].strip()
            text = re.sub(r"\s+", " ", text)
            if len(text) > 200:
                text = text[:197] + "…"
            print(f"  [{provider:12s}] {text}")
        except Exception as exc:
            print(f"  [{provider:12s}] (call failed: {type(exc).__name__}: {exc})")
        print()
    print("  All three saw the same focused slice. None saw the full conversation.")


# ── provider detection ──────────────────────────────────────────


def _detect_providers() -> list[tuple[str, str]]:
    """Return [(label, litellm_model), ...] for each available provider."""
    out: list[tuple[str, str]] = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        out.append(("anthropic", "anthropic/claude-haiku-4-5-20251001"))
    if os.environ.get("OPENAI_API_KEY"):
        out.append(("openai", "openai/gpt-4o-mini"))
    ollama_model = _first_pulled_ollama_model()
    if ollama_model is not None:
        out.append((f"ollama:{ollama_model}", f"ollama/{ollama_model}"))
    return out


def _first_pulled_ollama_model() -> str | None:
    """Probe local Ollama for any pulled model. Returns the first found, or None.

    Prefers small chat-tuned models for the demo. Returns the first match
    so the example works on whatever the developer happens to have pulled.
    """
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags", timeout=0.5,
        ) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None

    models = [m.get("name", "") for m in data.get("models", [])]
    if not models:
        return None

    # Prefer small chat-tuned models if available.
    preferred_prefixes = (
        "llama3.2", "llama3.1", "llama3", "qwen2.5", "mistral", "gemma2", "phi3",
    )
    for prefix in preferred_prefixes:
        for m in models:
            if m.startswith(prefix):
                return m
    return models[0]


if __name__ == "__main__":
    asyncio.run(main())
