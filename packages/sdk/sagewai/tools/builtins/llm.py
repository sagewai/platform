# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Platform-LLM tools that route through the Harness for cost-tier selection.

Tools:
- content_translate — translate text to a target language (complexity: low)
- quiz_generate     — generate quiz questions on a topic (complexity: medium)

Harness wiring
--------------
``_chat_completion`` is the internal indirection point. In production it
calls ``HarnessProxy.handle_request`` via a lazily-constructed minimal
proxy (no auth, no budget enforcement) with ``force_model_header`` set to
the tier-appropriate model from ``ModelTierConfig``.  The complexity_hint
values recognised here mirror ``ComplexityTier``: ``"low"`` → SIMPLE,
``"medium"`` → MEDIUM, ``"high"`` → COMPLEX.

Tests monkeypatch ``_chat_completion`` directly so the real Harness is
never imported during unit-test runs.

Return shape
------------
``_chat_completion`` always returns an object with:
- ``.content: str``  — the LLM's text reply
- ``.metadata: dict | None``  — optional source_lang etc.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sagewai.core._strategy_utils import parse_json

# Mapping from the public complexity_hint strings to ComplexityTier values.
_HINT_TO_TIER: dict[str, str] = {
    "low": "simple",
    "medium": "medium",
    "high": "complex",
}


async def _chat_completion(
    messages: list[dict[str, str]], *, complexity_hint: str
) -> Any:
    """Indirection so tests can monkeypatch without importing the Harness.

    Resolves a minimal HarnessProxy lazily.  The proxy is constructed with:
    - ``InMemoryHarnessStore`` (no persisted keys/spend)
    - ``HarnessRouter`` with default ``ModelTierConfig``
    - A ``LiteLLMProxyBackend`` pointed at the default provider

    ``complexity_hint`` is translated to the matching model via
    ``ModelTierConfig`` and passed as ``force_model_header`` so the
    Harness honours the caller's tier preference regardless of the
    heuristic classifier's opinion.

    The proxy response is an OpenAI-compat dict; we normalise it to a
    SimpleNamespace with ``.content`` and ``.metadata`` so the callers
    stay decoupled from the raw Harness shape.
    """
    from sagewai.harness.budget import HarnessBudgetManager
    from sagewai.harness.classifier import RequestClassifier
    from sagewai.harness.models import (
        ComplexityTier,
        HarnessConfig,
        HarnessIdentity,
        ModelTierConfig,
    )
    from sagewai.harness.policy import PolicyEngine
    from sagewai.harness.proxy import HarnessProxy
    from sagewai.harness.router import HarnessRouter
    from sagewai.harness.store import InMemoryHarnessStore

    tier_str = _HINT_TO_TIER.get(complexity_hint, "medium")
    tier = ComplexityTier(tier_str)

    tier_config = ModelTierConfig()
    target_model = tier_config.model_for_tier(tier)

    store = InMemoryHarnessStore()
    budget = HarnessBudgetManager.__new__(HarnessBudgetManager)
    # Minimal budget manager: borrow the real BudgetManager from admin.
    from sagewai.admin.budget import BudgetManager

    budget.__init__(BudgetManager())  # type: ignore[misc]

    router = HarnessRouter(
        classifier=RequestClassifier(),
        policy_engine=PolicyEngine(),
        budget_manager=budget,
        tier_config=tier_config,
        allow_override=True,
    )

    # Use a no-op backend that calls LiteLLM directly so there is no
    # network dependency at import time.  The LiteLLMProxyBackend
    # requires a running proxy server, so we construct a thin inline
    # backend that calls litellm.acompletion directly.
    from sagewai.harness.backend import LLMBackend  # noqa: F401 (type check)

    class _DirectLiteLLMBackend:
        """Thin inline backend — calls litellm.acompletion without a proxy."""

        async def chat_completion(
            self,
            *,
            model: str,
            messages: list[dict],
            stream: bool = False,
            tools: list[dict] | None = None,
            **kwargs: Any,
        ) -> dict:
            import litellm  # type: ignore[import-untyped]

            resp = await litellm.acompletion(
                model=model,
                messages=messages,
                stream=False,
                **({"tools": tools} if tools else {}),
                **kwargs,
            )
            # litellm returns a ModelResponse; convert to plain dict.
            return resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)

        async def list_models(self) -> list[str]:
            return [target_model]

    proxy = HarnessProxy(
        store=store,
        router=router,
        backends={"default": _DirectLiteLLMBackend()},
        config=HarnessConfig(transparency_headers=False),
    )

    # A minimal internal identity — no user auth required for builtin tools.
    identity = HarnessIdentity(
        key_id="builtin-tool",
        user_id="builtin",
        org_id="internal",
    )

    raw = await proxy.handle_request(
        identity=identity,
        messages=messages,
        model=target_model,
        force_model_header=target_model,
        stream=False,
    )

    # Extract content from OpenAI-compat response shape.
    try:
        content: str = raw["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        content = ""

    harness_meta: dict = raw.get("_harness", {}) if isinstance(raw, dict) else {}
    return SimpleNamespace(content=content, metadata=harness_meta or None)


async def content_translate(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate text to a target language using a SIMPLE-tier LLM call.

    Args:
        payload: dict with keys:
            - ``text`` (str, required) — text to translate (capped at 16 000 chars)
            - ``target_lang`` (str, required) — target language name or BCP-47 tag
            - ``source_lang`` (str, optional, default "auto") — source language
            - ``tone`` (str, optional, default "formal") — translation tone

    Returns:
        dict with:
            - ``translated`` (str) — the translated text
            - ``source_lang_detected`` (str | None) — source language if reported
    """
    text = payload["text"][:16_000]
    target = payload["target_lang"]
    source = payload.get("source_lang", "auto")
    tone = payload.get("tone", "formal")
    prompt = (
        f"Translate the following text to {target}. "
        f"Source language: {source}. Tone: {tone}. "
        f"Reply with the translation only, no commentary.\n\n{text}"
    )
    resp = await _chat_completion(
        [{"role": "user", "content": prompt}], complexity_hint="low",
    )
    metadata = getattr(resp, "metadata", None) or {}
    return {
        "translated": resp.content.strip(),
        "source_lang_detected": metadata.get("source_lang"),
    }


async def quiz_generate(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate quiz questions on a topic using a MEDIUM-tier LLM call.

    Args:
        payload: dict with keys:
            - ``topic`` (str, required) — subject matter for the quiz
            - ``num_questions`` (int, optional, default 5) — number of questions
            - ``difficulty`` (str, optional, default "medium") — easy/medium/hard
            - ``format`` (str, optional, default "multiple_choice") — question format

    Returns:
        dict with:
            - ``questions`` (list[dict]) — parsed question objects, each with
              ``q``, ``choices`` (list | None), ``answer``, ``explanation``

    Raises:
        json.JSONDecodeError: If the LLM response cannot be parsed as JSON.
    """
    topic = payload["topic"]
    n = int(payload.get("num_questions", 5))
    difficulty = payload.get("difficulty", "medium")
    fmt = payload.get("format", "multiple_choice")
    prompt = (
        f"Generate {n} {difficulty}-difficulty "
        f"{fmt.replace('_', ' ')} questions about: {topic}.\n"
        "Reply with ONLY a JSON array of objects: "
        '[{"q": "...", "choices": ["a","b","c","d"]|null, '
        '"answer": "...", "explanation": "..."}]'
    )
    resp = await _chat_completion(
        [{"role": "user", "content": prompt}], complexity_hint="medium",
    )
    questions = parse_json(resp.content)
    return {"questions": questions}
