# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Fresh, bounded TaskCapsule compilation from canonical state."""

from __future__ import annotations

import re
from typing import Any

from sagewai.work.contract import WorkContract
from sagewai.work.knowledge import KnowledgeItem, KnowledgeQuery, KnowledgeStore
from sagewai.work.models import TaskCapsule, WorkItem


def _has_meaningful_term_overlap(query: str, statement: str) -> bool:
    """Reject a generic one-token coincidence for multi-term searches."""
    query_terms = set(re.findall(r"[a-z0-9_]+", query.casefold()))
    statement_terms = set(re.findall(r"[a-z0-9_]+", statement.casefold()))
    overlap = query_terms & statement_terms
    if len(query_terms) == 1:
        return len(overlap) == 1
    return len(overlap) >= 2


class TaskCapsuleCompiler:
    """Compile direct evidence and scoped board search into one capsule."""

    def __init__(
        self,
        *,
        knowledge_store: KnowledgeStore,
        max_knowledge_items: int = 20,
    ) -> None:
        if max_knowledge_items < 1:
            raise ValueError("max_knowledge_items must be positive")
        self._knowledge_store = knowledge_store
        self._max_knowledge_items = max_knowledge_items

    async def compile(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        stage: str,
        search_text: str | None = None,
        open_assumption_ids: tuple[str, ...] = (),
        prior_result_refs: tuple[str, ...] = (),
        profile_context: dict[str, Any] | None = None,
    ) -> TaskCapsule:
        """Build a new capsule; direct item references precede FTS results."""
        if work_item.id != contract.work_id:
            raise ValueError("contract belongs to a different work item")
        if work_item.project_id != contract.project_id:
            raise ValueError("contract belongs to a different project")

        selected: list[KnowledgeItem] = []
        selected_ids: set[str] = set()
        project_id = work_item.project_id

        if project_id is not None:
            for item_id in contract.evidence_refs:
                item = await self._knowledge_store.get(item_id, project_id=project_id)
                if item is not None:
                    selected.append(item)
                    selected_ids.add(item.id)
                    if len(selected) == self._max_knowledge_items:
                        break

            for item_id in prior_result_refs:
                if len(selected) == self._max_knowledge_items:
                    break
                if item_id in selected_ids:
                    continue
                item = await self._knowledge_store.get(item_id, project_id=project_id)
                if item is None or item.work_id not in {None, work_item.id}:
                    continue
                selected.append(item)
                selected_ids.add(item.id)

            if search_text and len(selected) < self._max_knowledge_items:
                matches = await self._knowledge_store.search(
                    KnowledgeQuery(text=search_text, project_id=project_id)
                )
                for item in matches:
                    if item.id in selected_ids:
                        continue
                    if item.work_id not in {None, work_item.id}:
                        continue
                    selected.append(item)
                    selected_ids.add(item.id)
                    if len(selected) == self._max_knowledge_items:
                        break

                if len(selected) < self._max_knowledge_items:
                    candidates = await self._knowledge_store.search_high_importance_project_findings_any_term(
                        KnowledgeQuery(text=search_text, project_id=project_id)
                    )
                    for item in candidates:
                        if item.id in selected_ids:
                            continue
                        if not _has_meaningful_term_overlap(search_text, item.statement):
                            continue
                        selected.append(item)
                        selected_ids.add(item.id)
                        if len(selected) == self._max_knowledge_items:
                            break

        return TaskCapsule(
            project_id=project_id,
            work_id=work_item.id,
            stage=stage,
            work_item=work_item,
            contract=contract,
            knowledge_refs=tuple(item.id for item in selected),
            knowledge_items=tuple(selected),
            open_assumption_ids=open_assumption_ids,
            prior_result_refs=prior_result_refs,
            profile_context=profile_context or {},
        )
