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
"""Example 51 — A long document on a small local model.

You want cheap, private, local inference — an Ollama model on your own
hardware. But your input is a 16-clause vendor contract that is several
times larger than the model's context window. The model cannot read it.

``@transform(summarize, @context('the contract'))`` fixes this. The
transform compresses the document inline — *before* the small model sees
it — so a model with a small window can answer questions about a far
larger input. The directive engine runs the transform during prompt
resolution; the small model only ever sees the compressed result.

The example also registers a **custom operation**. A transform operation
is just an async function: ``transform.register("extract_clauses", fn)``
adds a deterministic clause-extractor with no LLM at all, invoked the same
way — ``@transform(extract_clauses, @context('the contract'))``. The
custom op returns a bare string; the engine wraps it into a result.

Offline by default
------------------
With no flag set, ``summarize`` uses a stub LLM client that returns a
canned summary, so the example runs with no API key and no network. Set
``SAGEWAI_TRANSFORM_LIVE=1`` to summarize with a real local model via
litellm (``SAGEWAI_TRANSFORM_MODEL``, default ``ollama/llama3.2:3b`` —
start Ollama and ``ollama pull`` the model first).

What's exercised
----------------
- ``@transform(summarize, ...)`` — compress an input larger than the window
- ``@transform(extract_clauses, ...)`` — a custom registered operation
- :meth:`TransformRegistry.register` — the "run a function" registry
- :func:`~sagewai.transform.register_transform_directive` — the directive adapter
- :class:`~sagewai.directives.engine.DirectiveEngine` — small-model profile

Usage::

    python 51_big_input_small_model.py
    SAGEWAI_TRANSFORM_LIVE=1 python 51_big_input_small_model.py
"""

from __future__ import annotations

import asyncio
import os
import re

from sagewai.directives.engine import DirectiveEngine
from sagewai.transform import (
    TransformEngine,
    TransformRegistry,
    register_transform_directive,
    summarize,
)

# A representative small/local-model context window. Real small models sit
# in the 4K–8K range; the contract below is several times larger.
SMALL_MODEL_WINDOW_TOKENS = 4096


# ── the long document ────────────────────────────────────────────────


_CLAUSES: tuple[tuple[str, str], ...] = (
    ("TERM", "This Agreement begins on the Effective Date and continues for "
     "an initial term of twenty-four (24) months. It renews automatically "
     "for successive twelve (12) month terms unless either party gives "
     "written notice of non-renewal at least sixty (60) days before the "
     "end of the then-current term."),
    ("SERVICES", "Vendor shall provide the cloud infrastructure services "
     "described in Exhibit A, including compute, managed storage, and "
     "network capacity. Vendor may update the technical implementation of "
     "the services provided the updates do not materially degrade "
     "functionality or performance."),
    ("FEES AND PAYMENT", "Customer shall pay the fees set out in Exhibit B "
     "within thirty (30) days of each invoice date. Late amounts accrue "
     "interest at 1.0% per month. Fees are exclusive of taxes, which are "
     "the responsibility of Customer except for taxes on Vendor's net "
     "income."),
    ("SERVICE LEVELS", "Vendor warrants a monthly uptime of 99.9% for the "
     "production services, measured per the methodology in Exhibit C. If "
     "uptime falls below the commitment, Customer is entitled to the "
     "service credits set out in that Exhibit as its sole remedy for "
     "availability failures."),
    ("DATA PROTECTION", "Vendor shall process personal data only on "
     "documented instructions from Customer and solely to deliver the "
     "services. Vendor shall maintain technical and organizational "
     "measures appropriate to the risk, and shall notify Customer without "
     "undue delay after becoming aware of a personal data breach."),
    ("SECURITY", "Vendor shall maintain an information security program "
     "aligned to ISO 27001, including encryption of data in transit and "
     "at rest, role-based access control, and annual third-party "
     "penetration testing. Customer may review Vendor's most recent audit "
     "report once per contract year."),
    ("CONFIDENTIALITY", "Each party shall protect the other's Confidential "
     "Information with the same care it uses for its own, and not less "
     "than reasonable care. Confidential Information may be used only to "
     "perform under this Agreement and disclosed only to personnel with a "
     "need to know."),
    ("INTELLECTUAL PROPERTY", "Each party retains ownership of its "
     "pre-existing intellectual property. Vendor grants Customer a "
     "non-exclusive, non-transferable license to use the services during "
     "the term. Customer owns all Customer Data and grants Vendor only the "
     "rights needed to provide the services."),
    ("CUSTOMER DATA", "Customer Data remains the property of Customer. On "
     "termination, Vendor shall make Customer Data available for export "
     "for thirty (30) days, after which Vendor shall delete it from "
     "production systems within ninety (90) days, subject to legal "
     "retention requirements."),
    ("WARRANTIES", "Each party warrants that it has the authority to enter "
     "into this Agreement. Vendor warrants that the services will be "
     "performed in a professional and workmanlike manner. Except as "
     "expressly stated, the services are provided \"as is\" without "
     "further warranty of any kind."),
    ("INDEMNIFICATION", "Vendor shall defend Customer against third-party "
     "claims that the services infringe a patent, copyright, or trade "
     "secret, and shall pay resulting damages finally awarded. Customer "
     "shall defend Vendor against claims arising from Customer Data or "
     "Customer's misuse of the services."),
    ("LIMITATION OF LIABILITY", "Neither party is liable for indirect, "
     "incidental, or consequential damages. Each party's total aggregate "
     "liability is capped at the fees paid or payable in the twelve (12) "
     "months preceding the claim. The cap does not apply to breaches of "
     "confidentiality or a party's indemnification obligations."),
    ("TERMINATION", "Either party may terminate for material breach not "
     "cured within thirty (30) days of written notice. Either party may "
     "terminate immediately if the other becomes insolvent. On "
     "termination, Customer shall pay all fees accrued through the "
     "effective date of termination."),
    ("SUSPENSION", "Vendor may suspend the services if Customer's use "
     "poses a security risk to the platform or to other customers, or if "
     "fees are more than thirty (30) days overdue. Vendor shall give "
     "advance notice where practicable and shall restore the services "
     "promptly once the cause is resolved."),
    ("FORCE MAJEURE", "Neither party is liable for a failure to perform "
     "caused by events beyond its reasonable control, including natural "
     "disasters, war, and large-scale infrastructure outages. The "
     "affected party shall use reasonable efforts to mitigate and shall "
     "resume performance as soon as practicable."),
    ("GOVERNING LAW", "This Agreement is governed by the laws of the State "
     "of Delaware, without regard to its conflict-of-laws rules. The "
     "parties submit to the exclusive jurisdiction of the state and "
     "federal courts located in Delaware for any dispute not resolved "
     "through good-faith negotiation."),
)


# Generic sub-paragraph boilerplate appended under every clause — the kind
# of dense, repetitive text that bulks up a real contract well past a small
# model's window.
_SUBCLAUSES: tuple[str, ...] = (
    "Each party shall perform its obligations under this Section in good "
    "faith and in compliance with all applicable laws, regulations, and "
    "industry standards, and shall promptly notify the other party in "
    "writing of any circumstance that it reasonably believes will prevent "
    "or materially delay performance, together with a proposed plan of "
    "remediation and an estimated date by which performance will resume.",
    "Any notice, consent, or approval required under this Section shall be "
    "in writing and delivered to the address recorded in the signature "
    "block, and shall be deemed effective upon confirmed delivery; either "
    "party may update its notice address on ten (10) business days written "
    "notice, and a failure to maintain a current address shall not excuse "
    "that party from notices properly sent to the last address of record.",
    "The rights and remedies of the parties under this Section are "
    "cumulative and in addition to any other rights or remedies available "
    "at law or in equity, and no exercise of, or failure to exercise, any "
    "right under this Section shall operate as a waiver of that or any "
    "other right; any waiver must be in writing and signed by the party "
    "against whom the waiver is asserted to be effective.",
    "If any provision of this Section is held by a court of competent "
    "jurisdiction to be invalid, illegal, or unenforceable, that provision "
    "shall be modified to the minimum extent necessary to make it "
    "enforceable while preserving the parties' original intent, and the "
    "remaining provisions of this Section and of this Agreement shall "
    "continue in full force and effect without being impaired or "
    "invalidated in any way.",
)


def _build_contract() -> str:
    """Assemble the synthetic vendor contract — 16 clauses, each with four
    sub-paragraphs — a document several times a small model's window."""
    parts = [
        "VENDOR SERVICES AGREEMENT",
        'between Globex Corporation ("Customer") and Acme Cloud '
        'Infrastructure, Inc. ("Vendor").',
        "Effective Date: 2026-05-01.",
        "",
    ]
    for index, (title, body) in enumerate(_CLAUSES, start=1):
        parts.append(f"{index}. {title}.")
        parts.append(body)
        for sub_index, sub in enumerate(_SUBCLAUSES, start=1):
            parts.append(f"  {index}.{sub_index}  {sub}")
        parts.append("")
    return "\n".join(parts)


def _est_tokens(text: str) -> int:
    """Rough token estimate — ~4 characters per token."""
    return max(1, len(text) // 4)


# ── canned summary (offline mode) ────────────────────────────────────


_CANNED_SUMMARY = (
    "A 24-month auto-renewing cloud-services agreement between Globex "
    "(Customer) and Acme (Vendor). Vendor provides compute, storage, and "
    "network capacity at a 99.9% uptime commitment backed by service "
    "credits. Fees are net-30. Vendor processes personal data only on "
    "Customer instruction under an ISO 27001 security program. Liability "
    "is capped at trailing-12-month fees, excluding confidentiality and "
    "indemnity breaches. Either party may terminate for uncured 30-day "
    "material breach; Customer Data is exportable for 30 days post-term. "
    "Governing law: Delaware."
)


class _StubLLM:
    """A duck-typed LLM client returning a fixed completion (offline mode)."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def acompletion(self, *, messages, **_):
        return {"choices": [{"message": {"content": self._text}}]}


# ── custom transform operation — "run a function" ────────────────────


async def extract_clauses(content: str, *, project_id=None, **params) -> str:
    """A custom transform operation: pull clause headings from a contract.

    A registered operation is just an async function — no LLM required. It
    returns a bare string, which the engine wraps into a TransformResult.
    An optional ``keyword`` param filters the headings.
    """
    keyword = str(params.get("keyword", "")).lower()
    headings: list[str] = []
    for line in content.splitlines():
        match = re.match(r"\s*(\d+)\.\s+([A-Z][A-Z &]+)\.", line)
        if match:
            heading = f"{match.group(1)}. {match.group(2).strip()}"
            if not keyword or keyword in heading.lower():
                headings.append(heading)
    if not headings:
        return "no matching clauses found"
    return f"{len(headings)} clauses — " + "; ".join(headings)


# ── transform engine wiring ──────────────────────────────────────────


def _build_transform_engine(live: bool) -> TransformEngine:
    """A TransformEngine with ``summarize`` plus the custom ``extract_clauses``."""
    registry = TransformRegistry()

    if live:
        model = os.environ.get("SAGEWAI_TRANSFORM_MODEL", "ollama/llama3.2:3b")

        async def _summarize_op(content, *, project_id=None, **params):
            params.setdefault("model", model)
            return await summarize(content, **params)

    else:
        stub = _StubLLM(_CANNED_SUMMARY)

        async def _summarize_op(content, *, project_id=None, **params):
            return await summarize(content, llm=stub, **params)

    registry.register("summarize", _summarize_op)
    # The "run a function" registry — a custom op is a plain async callable.
    registry.register("extract_clauses", extract_clauses)
    return TransformEngine(registry)


# ── context provider ─────────────────────────────────────────────────


class _DocumentContext:
    """A duck-typed context provider serving the contract to @context(...)."""

    def __init__(self, document: str) -> None:
        self._document = document

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        return [self._document]


# ── output helpers ───────────────────────────────────────────────────


def _rule(title: str = "") -> None:
    if title:
        print(f"\n─── {title} " + "─" * max(2, 64 - len(title)))
    else:
        print("─" * 70)


def _transform_digest(result) -> str:
    """The TransformResult.output the @transform directive injected."""
    for resolved in result.directives_found:
        if resolved.source == "custom:transform" and resolved.content:
            return resolved.content
    return "(transform produced no output)"


# ── main ─────────────────────────────────────────────────────────────


async def main() -> None:
    live = bool(os.environ.get("SAGEWAI_TRANSFORM_LIVE"))

    _rule()
    print(" Sagewai — a long document on a small model (example 51, @transform)")
    _rule()
    print(f"  mode: {'LIVE (real local model)' if live else 'offline (stub LLM)'}")

    contract = _build_contract()
    doc_tokens = _est_tokens(contract)

    _rule("The problem")
    print(f"  The vendor contract is {len(_CLAUSES)} clauses, "
          f"~{doc_tokens} tokens (~{len(contract)} chars).")
    print(f"  The target model's context window is {SMALL_MODEL_WINDOW_TOKENS} tokens.")
    over = doc_tokens / SMALL_MODEL_WINDOW_TOKENS
    print(f"  The document is {over:.1f}× the window — the model cannot read it whole.")

    # A small-model directive engine; @context serves the contract.
    context = _DocumentContext(contract)
    engine = DirectiveEngine(context=context, model="ollama/llama3.2:3b")
    register_transform_directive(engine, transform_engine=_build_transform_engine(live))

    # ── summarize: compress the document to fit ──
    _rule("@transform(summarize, ...) — compress to fit the window")
    result = await engine.resolve(
        "@transform(summarize, @context('the contract'))"
    )
    summary = _transform_digest(result)
    summary_tokens = _est_tokens(summary)
    print(f"  directive: @transform(summarize, @context('the contract'))")
    print()
    print("  injected summary:")
    for line in _wrap(summary, 66):
        print(f"    {line}")
    print()
    print(f"  {doc_tokens} tokens → {summary_tokens} tokens "
          f"({doc_tokens / max(1, summary_tokens):.0f}× smaller) — "
          f"now well inside the {SMALL_MODEL_WINDOW_TOKENS}-token window.")

    # ── extract_clauses: a custom registered operation ──
    _rule("@transform(extract_clauses, ...) — a custom registered op")
    print('  registered with: registry.register("extract_clauses", extract_clauses)')
    print("  extract_clauses is a plain async function — no LLM involved.")
    print()
    result = await engine.resolve(
        "@transform(extract_clauses, @context('the contract'))"
    )
    clauses = _transform_digest(result)
    print("  injected clause index:")
    for line in _wrap(clauses, 66):
        print(f"    {line}")
    print()
    # The same custom op with a param.
    result = await engine.resolve(
        "@transform(extract_clauses, @context('the contract'), keyword=data)"
    )
    print("  with keyword=data → " + _transform_digest(result))

    _rule()
    print("  The directive engine is the bridge: it does the transform work")
    print("  declaratively, pre-LLM, so a small local model punches far above")
    print("  its context window — and a custom op is just a function you register.")
    _rule()


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word-wrap for readable console output."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    asyncio.run(main())
