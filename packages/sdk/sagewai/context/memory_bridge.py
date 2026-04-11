# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""MemoryBridge — converts agent interactions into persistent context.

Bridges the gap between ephemeral conversations and the persistent context
store. Handles fact extraction from conversations, workflow output storage,
and conflict detection when new info contradicts existing knowledge.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sagewai.context.ingestion import _FALLBACK_ERRORS
from sagewai.context.models import (
    ContextDocument,
    ContextScope,
    ContextSource,
)
from sagewai.context.stores import ContextMetadataStore, ContextVectorStore
from sagewai.intelligence.extractors.protocol import FactExtractor
from sagewai.models.message import ChatMessage

logger = logging.getLogger(__name__)


async def _call_extraction_llm(model: str, prompt: str) -> list[str]:
    """Call LLM to extract facts from text."""
    try:
        import litellm

        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=500,
        )
        text = response.choices[0].message.content or "[]"
        try:
            facts = json.loads(text)
            if isinstance(facts, list):
                return [str(f) for f in facts]
        except json.JSONDecodeError:
            lines = [line.strip().lstrip("- \u2022").strip() for line in text.split("\n")]
            return [line for line in lines if line]
    except _FALLBACK_ERRORS:
        logger.warning("LLM extraction failed, returning empty facts", exc_info=True)
    return []


class MemoryBridge:
    """Converts agent interactions into persistent context.

    Handles:
    - Conversation fact extraction (decisions, preferences, entities)
    - Workflow output storage (results, artifacts, conclusions)
    - Web research result ingestion
    - Update-vs-append: checks if a fact updates an existing one

    Usage::

        bridge = MemoryBridge(context_engine=engine)
        docs = await bridge.extract_from_conversation(
            messages, scope=ContextScope.PROJECT, scope_id="my-project"
        )
    """

    def __init__(
        self,
        *,
        context_engine: Any,  # ContextEngine — avoid circular import
        model: str = "gpt-4o-mini",
        extract_every_n_turns: int = 5,
        fact_extractor: FactExtractor | None = None,
    ) -> None:
        self.context_engine = context_engine
        self.model = model
        self.extract_every_n_turns = extract_every_n_turns
        self._fact_extractor = fact_extractor

    def should_extract(self, turn_count: int, compaction_happened: bool = False) -> bool:
        """Determine whether extraction should run this turn."""
        if compaction_happened:
            return True
        return turn_count > 0 and turn_count % self.extract_every_n_turns == 0

    async def extract_from_conversation(
        self,
        messages: list[ChatMessage],
        scope: ContextScope,
        scope_id: str,
    ) -> list[ContextDocument]:
        """Extract key facts from a conversation and store as context.

        Returns a list of ContextDocuments created (one per fact batch).
        """
        if not messages:
            return []

        conversation_text = "\n".join(
            f"{msg.role.value.upper()}: {msg.content or '[tool interaction]'}"
            for msg in messages
            if msg.content
        )

        # Use pluggable fact extractor when available
        if self._fact_extractor is not None:
            extracted = await self._fact_extractor.extract(conversation_text)
            facts = [f.content for f in extracted]
        else:
            prompt = (
                "Extract key facts, decisions, and user preferences from this "
                "conversation. Return a JSON array of strings, each being one "
                "concise fact. Only include information worth remembering for "
                "future conversations. If there is nothing noteworthy, "
                "return [].\n\n"
                f"{conversation_text}\n\n"
                "Facts (JSON array):"
            )
            facts = await _call_extraction_llm(self.model, prompt)

        if not facts:
            return []

        # Store all facts as a single document
        facts_text = "\n".join(f"- {f}" for f in facts)
        doc = await self.context_engine.ingest_text(
            text=facts_text,
            title=f"Conversation facts ({len(facts)} items)",
            scope=scope,
            scope_id=scope_id,
            source=ContextSource.CONVERSATION,
            metadata={"fact_count": len(facts), "facts": facts},
        )

        logger.info(
            "MemoryBridge: extracted %d facts from conversation → doc %s",
            len(facts),
            doc.id,
        )
        return [doc]

    async def store_workflow_output(
        self,
        workflow_id: str,
        output: str,
        scope: ContextScope,
        scope_id: str,
        title: str | None = None,
    ) -> ContextDocument:
        """Store workflow output as persistent context."""
        doc = await self.context_engine.ingest_text(
            text=output,
            title=title or f"Workflow output ({workflow_id})",
            scope=scope,
            scope_id=scope_id,
            source=ContextSource.WORKFLOW,
            metadata={"workflow_id": workflow_id},
        )
        logger.info("MemoryBridge: stored workflow %s output → doc %s", workflow_id, doc.id)
        return doc

    async def store_research(
        self,
        query: str,
        results: list[str],
        scope: ContextScope,
        scope_id: str,
    ) -> ContextDocument:
        """Store web research results as persistent context."""
        text = f"Research query: {query}\n\n"
        text += "\n\n".join(f"Result {i + 1}:\n{r}" for i, r in enumerate(results))

        doc = await self.context_engine.ingest_text(
            text=text,
            title=f"Research: {query[:60]}",
            scope=scope,
            scope_id=scope_id,
            source=ContextSource.RESEARCH,
            metadata={"query": query, "result_count": len(results)},
        )
        logger.info("MemoryBridge: stored research for '%s' → doc %s", query[:40], doc.id)
        return doc
