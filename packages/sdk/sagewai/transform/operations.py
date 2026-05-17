# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Built-in transform operations: ``summarize`` and ``graphify``."""

from __future__ import annotations

from typing import Any

from sagewai.transform.models import TransformResult

_SUMMARIZE_PROMPT = (
    "Summarise the text below in {max_words} words or fewer. "
    "Return only the summary — no preamble, no markdown."
)

# Small/cheap default — the transform capability runs on, and is usable by,
# small models. Callers may override via the ``model`` param.
_DEFAULT_MODEL = "gpt-4o-mini"


class _LiteLLMClient:
    """Minimal duck-typed LLM client wrapping ``litellm`` for a fixed model.

    Normalises litellm's response into the ``{"choices": [...]}`` dict shape
    that ``summarize`` (and the memory strategies) expect.
    """

    def __init__(self, model: str) -> None:
        self._model = model

    async def acompletion(self, *, messages: list[dict], **kwargs: Any) -> dict:
        import litellm

        resp = await litellm.acompletion(
            model=self._model, messages=messages, **kwargs
        )
        content = resp.choices[0].message.content or ""
        return {"choices": [{"message": {"content": content}}]}


async def summarize(
    content: str,
    *,
    llm: Any = None,
    max_words: int = 200,
    project_id: str | None = None,
    model: str = _DEFAULT_MODEL,
    **_: Any,
) -> TransformResult:
    """Compress ``content`` into a short summary via one LLM call.

    ``llm`` is a duck-typed client exposing ``async acompletion(*, messages)``
    — the same shape the memory strategies use. When not injected, a default
    client is built on ``model`` (small/cheap by default). ``max_words`` caps
    the requested summary length (default 200).
    """
    if llm is None:
        llm = _LiteLLMClient(model)
    resp = await llm.acompletion(
        messages=[
            {"role": "system", "content": _SUMMARIZE_PROMPT.format(max_words=max_words)},
            {"role": "user", "content": content},
        ],
    )
    text = resp["choices"][0]["message"]["content"].strip()
    return TransformResult(operation="summarize", output=text, ok=True)


async def graphify(
    content: str,
    *,
    extractor: Any = None,
    graph: Any = None,
    project_id: str | None = None,
    model: str = _DEFAULT_MODEL,
    **_: Any,
) -> TransformResult:
    """Distil ``content`` into relation triples written to graph memory.

    Uses :class:`~sagewai.intelligence.extractors.llm_extractor.LLMRelationExtractor`
    (``parse_json``-robust, so a fenced SLM response still yields clean
    triples) to extract ``subject → predicate → object`` triples, then writes
    each into the project-scoped :class:`~sagewai.memory.graph.GraphMemory`.

    ``extractor`` and ``graph`` may be injected (for tests); otherwise the
    defaults are constructed — the extractor on ``model``, the graph scoped to
    ``project_id``. Extracting zero triples is a success, not a failure.
    """
    if extractor is None:
        from sagewai.intelligence.extractors.llm_extractor import LLMRelationExtractor

        extractor = LLMRelationExtractor(model=model)
    if graph is None:
        from sagewai.memory.graph import GraphMemory

        graph = GraphMemory(project_id=project_id)

    triples = await extractor.extract(content)
    for triple in triples:
        await graph.add_relation(triple.subject, triple.predicate, triple.object)

    count = len(triples)
    if count == 0:
        output = "no relations extracted"
    else:
        preview = "; ".join(
            f"{t.subject}→{t.predicate}→{t.object}" for t in triples[:3]
        )
        output = f"{count} relations into graph memory: {preview}"
        if count > 3:
            output += "; …"

    return TransformResult(
        operation="graphify",
        output=output,
        ok=True,
        metadata={"relations_written": count},
    )
