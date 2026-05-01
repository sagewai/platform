# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Memory extraction strategies: semantic, preference, and summary strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class TurnEvent:
    """A single turn in a conversation.

    Represents one message in a conversation, tracking the speaker role,
    content, session identifier, and optional metadata.
    """

    role: str
    content: str
    session_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExtractedRecord:
    """A memory record extracted by a strategy.

    Represents a single piece of structured information extracted from
    conversation turns by a memory extraction strategy.
    """

    namespace: str
    content: str
    source_session: str
    strategy: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MemoryStrategy(Protocol):
    """Protocol for memory extraction strategies.

    Implementations must provide:
    - ``name`` — human-readable strategy name
    - ``namespace`` — extraction namespace (e.g. "preferences", "summaries")
    - ``extract()`` — async method to extract records from turns
    """

    name: str
    namespace: str

    async def extract(self, turns: list[TurnEvent]) -> list[ExtractedRecord]: ...
