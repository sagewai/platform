# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Mission runtime instance: a blueprint bound to concrete slot values.

A :class:`Mission` is the *runtime* counterpart to a
:class:`sagewai.autopilot.blueprint.Blueprint`. It owns:

* the blueprint id and version it was bound from,
* the project it belongs to (for multi-tenant isolation),
* the validated slot dictionary,
* the lifecycle state,
* a unique mission id.

Missions are mutable in exactly one dimension — their
:class:`~sagewai.autopilot._types.MissionState`. Any change must go
through :meth:`transition_to`, which enforces the allowed transition
graph below:

.. code-block:: text

                +-------+
                | DRAFT |
                +---+---+
                    |
                    v
               +----------+
               | APPROVED |<----+
               +----+-----+     |
                    |           |
                    v           |
              +-----------+     |
              | SCHEDULED |-----+
              +-----+-----+
                    |
                    v
              +---------+
              | RUNNING |
              +---+-----+
                  | |
          +-------+ +------+
          v                v
     +-----------+    +--------+
     | COMPLETED |    | FAILED |   (both terminal)
     +-----------+    +--------+
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from ._types import MissionState
from .blueprint import Blueprint
from .errors import MissionLifecycleError
from .validators import ValidatorRegistry

if TYPE_CHECKING:
    from sagewai.memory.rag import RAGEngine

# Explicit transition table. Terminal states (COMPLETED, FAILED) map
# to the empty set.
_ALLOWED_TRANSITIONS: dict[MissionState, set[MissionState]] = {
    MissionState.DRAFT: {MissionState.APPROVED},
    MissionState.APPROVED: {MissionState.SCHEDULED},
    MissionState.SCHEDULED: {MissionState.APPROVED, MissionState.RUNNING},
    MissionState.RUNNING: {MissionState.COMPLETED, MissionState.FAILED},
    MissionState.COMPLETED: set(),
    MissionState.FAILED: set(),
}


class Mission:
    """Runtime instance of a blueprint bound to concrete slot values.

    Construct via :meth:`from_blueprint`, never by calling the
    initializer directly.
    """

    __slots__ = (
        "mission_id",
        "project_id",
        "blueprint_id",
        "blueprint_version",
        "slots",
        "_state",
        "memory",
    )

    def __init__(
        self,
        *,
        mission_id: str,
        project_id: str,
        blueprint_id: str,
        blueprint_version: str,
        slots: dict[str, Any],
        memory: RAGEngine | None = None,
    ) -> None:
        self.mission_id = mission_id
        self.project_id = project_id
        self.blueprint_id = blueprint_id
        self.blueprint_version = blueprint_version
        self.slots = slots
        self._state: MissionState = MissionState.DRAFT
        self.memory = memory

        # Scope RAG memory to this mission so per-mission branches isolate state.
        if memory is not None:
            from sagewai.memory.branch import MemoryBranch
            from sagewai.memory.rag import RAGEngine as _RAGEngine

            if isinstance(memory, _RAGEngine):
                new_branch = MemoryBranch(mission_id=self.mission_id)
                # Refuse to silently steal a RAGEngine that's already scoped to a
                # different mission — a shared engine across missions would overwrite
                # branches in last-writer-wins fashion. Callers that need a shared
                # engine must use one mission per engine, or wait for issue #195.
                if memory._branch != MemoryBranch.global_root() and memory._branch != new_branch:
                    raise ValueError(
                        f"RAGEngine is already scoped to {memory._branch.mission_id!r}; "
                        f"refusing to re-stamp for mission {self.mission_id!r}. "
                        "Construct a separate RAGEngine per Mission."
                    )
                memory._branch = new_branch

    @property
    def state(self) -> MissionState:
        return self._state

    def transition_to(self, new_state: MissionState) -> None:
        allowed = _ALLOWED_TRANSITIONS[self._state]
        if new_state not in allowed:
            raise MissionLifecycleError(
                from_state=self._state.value,
                to_state=new_state.value,
            )
        self._state = new_state

    @classmethod
    def from_blueprint(
        cls,
        blueprint: Blueprint,
        *,
        project_id: str,
        slots: dict[str, Any],
        registry: ValidatorRegistry,
    ) -> Mission:
        """Validate ``slots`` against ``blueprint`` and return a draft mission."""
        validated = blueprint.validate_slots(slots, registry=registry)
        return cls(
            mission_id=f"ms-{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            blueprint_id=blueprint.id,
            blueprint_version=blueprint.version,
            slots=validated,
        )
