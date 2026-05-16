# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Memory branching: per-mission namespace isolation for RAG."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryBranch:
    """Namespace helper for per-mission memory isolation.

    A MemoryBranch provides scoped namespace prefixes for memory storage,
    allowing different missions to maintain isolated semantic and preference
    stores. The frozen+slots design makes instances hashable and immutable.
    """

    mission_id: str

    def scoped(self, namespace: str) -> str:
        """Return a scoped namespace by prefixing with the mission ID.

        Args:
            namespace: The memory namespace (e.g., "semantic", "preferences").

        Returns:
            Scoped namespace string in the format "mission_id/namespace".
        """
        return f"{self.mission_id}/{namespace}"

    @classmethod
    def global_root(cls) -> MemoryBranch:
        """Create a global root branch for org-wide memory.

        Returns:
            A MemoryBranch with mission_id="_global".
        """
        return cls(mission_id="_global")
