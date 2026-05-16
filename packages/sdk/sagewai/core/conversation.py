# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""ConversationManager — stateful multi-turn chat with memory and sessions.

Wraps a BaseAgent with conversation state management, auto-compaction,
memory read/write, and session persistence. This is the high-level API
for interactive chat experiences.

Usage::

    from sagewai.core.conversation import ConversationManager
    from sagewai.engines.universal import UniversalAgent

    agent = UniversalAgent(name="assistant", model="gpt-4o")
    manager = ConversationManager(agent=agent)

    response = await manager.send("Hello!")
    response = await manager.send("What did I just say?")  # has full history
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sagewai.core.compactor import LLMCompactor, PromptCompactor
from sagewai.core.session import SessionRecord, SessionStore
from sagewai.core.context import get_current_project
from sagewai.models.message import ChatMessage

logger = logging.getLogger(__name__)


class ConversationManager:
    """Stateful wrapper around a BaseAgent for multi-turn chat.

    Parameters
    ----------
    agent:
        Any agent with ``chat_with_history(messages)`` and ``config.name``.
    session_id:
        Unique session identifier. Auto-generated if omitted.
    compactor:
        PromptCompactor or LLMCompactor for context compression.
    memory:
        Optional MemoryProvider for retrieval and auto-write.
    memory_writer:
        Optional MemoryWriter for auto-extracting facts.
    session_store:
        Optional SessionStore for persistence and resumption.
    """

    def __init__(
        self,
        agent: Any,
        *,
        session_id: str | None = None,
        compactor: PromptCompactor | None = None,
        memory: Any = None,
        memory_writer: Any = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self._agent = agent
        self._session_id = session_id or str(uuid.uuid4())
        self._compactor = compactor
        self._memory = memory
        self._memory_writer = memory_writer
        self._session_store = session_store
        self._turn_count = 0
        self._summary = ""

        # Initialize with system prompt
        self._messages: list[ChatMessage] = []
        system_prompt = getattr(agent.config, "system_prompt", "")
        if system_prompt:
            self._messages.append(ChatMessage.system(system_prompt))

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def messages(self) -> list[ChatMessage]:
        return list(self._messages)

    @property
    def turn_count(self) -> int:
        return self._turn_count

    async def send(self, message: str) -> str:
        """Send a user message and return the agent's response.

        This method:
        1. Appends the user message
        2. Injects memory context (project-scoped)
        3. Auto-compacts if needed
        4. Calls the agent
        5. Appends the response
        6. Auto-saves the session
        7. Auto-extracts memory (periodically)
        """
        # 1. Append user message
        self._messages.append(ChatMessage.user(message))

        # 2. Inject memory context
        if self._memory:
            await self._inject_memory(message)

        # 3. Auto-compact
        compaction_happened = await self._maybe_compact()

        # 4. Call agent
        result = await self._agent.chat_with_history(list(self._messages))

        # 5. Append response
        self._messages.append(result)
        self._turn_count += 1

        # 6. Auto-save session
        await self._save_session()

        # 7. Auto-extract memory
        if self._memory_writer and self._memory:
            if self._memory_writer.should_extract(
                self._turn_count, compaction_happened=compaction_happened
            ):
                try:
                    await self._memory_writer.extract_and_store(self._messages, self._memory)
                except Exception:
                    logger.exception("Memory extraction failed")

        return result.content or ""

    async def resume(self) -> None:
        """Resume a previous session from the session store.

        Loads the stored messages and summary, rebuilding conversation state.
        """
        if not self._session_store:
            return

        project = get_current_project()
        project_id = project.project_id if project else None

        record = await self._session_store.load(self._session_id, project_id=project_id)
        if record is None:
            return

        # Rebuild messages
        self._messages = []
        system_prompt = getattr(self._agent.config, "system_prompt", "")
        if system_prompt:
            self._messages.append(ChatMessage.system(system_prompt))

        # Inject stored summary as context
        if record.summary:
            self._summary = record.summary
            self._messages.append(
                ChatMessage.system(f"[Previous conversation summary]\n{record.summary}")
            )

        # Restore recent messages
        for msg_data in record.messages:
            self._messages.append(ChatMessage(**msg_data))

        logger.info("Resumed session %s with %d messages", self._session_id, len(record.messages))

    def reset(self) -> None:
        """Clear conversation state, keeping only the system prompt."""
        self._messages = []
        system_prompt = getattr(self._agent.config, "system_prompt", "")
        if system_prompt:
            self._messages.append(ChatMessage.system(system_prompt))
        self._turn_count = 0
        self._summary = ""

    async def _inject_memory(self, query: str) -> None:
        """Retrieve relevant context from memory and inject into messages."""
        try:
            context_items = await self._memory.retrieve(query)
        except Exception:
            logger.exception("Memory retrieval failed")
            return

        if not context_items:
            return

        context_text = "\n\n".join(context_items)
        context_msg = ChatMessage.system(f"[Relevant context from memory]\n{context_text}")

        # Insert after system messages, before user messages
        insert_idx = 0
        for i, msg in enumerate(self._messages):
            if msg.role.value == "system":
                insert_idx = i + 1
            else:
                break
        self._messages.insert(insert_idx, context_msg)

    async def _maybe_compact(self) -> bool:
        """Compact messages if over threshold. Returns True if compacted."""
        if not self._compactor:
            return False

        if not self._compactor.needs_compaction(self._messages):
            return False

        if isinstance(self._compactor, LLMCompactor):
            self._messages = await self._compactor.compact_async(self._messages)
        else:
            self._messages = self._compactor.compact(self._messages)

        logger.info("Conversation compacted to %d messages", len(self._messages))
        return True

    async def _save_session(self) -> None:
        """Save current state to session store."""
        if not self._session_store:
            return

        project = get_current_project()
        project_id = project.project_id if project else None

        # Serialize non-system messages for storage
        serializable_msgs = [
            msg.model_dump() for msg in self._messages if msg.role.value != "system"
        ]

        record = SessionRecord(
            session_id=self._session_id,
            project_id=project_id,
            agent_name=self._agent.config.name,
            messages=serializable_msgs,
            summary=self._summary,
        )
        try:
            await self._session_store.save(record)
        except Exception:
            logger.exception("Failed to save session %s", self._session_id)
