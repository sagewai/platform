# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""In-process agent registry for multi-agent orchestration.

Allows agents to register their capabilities and enables orchestrators
to discover and delegate tasks to the most suitable agent.

Usage::

    from sagewai.core.registry import AgentRegistry

    registry = AgentRegistry()
    registry.register(scout_agent, capabilities=["research", "search"])
    registry.register(writer_agent, capabilities=["writing", "drafting"])

    agents = registry.discover("research")  # [scout_agent]
    result = await registry.delegate("research", "Find info about AI trends")
"""

from __future__ import annotations

import logging
import threading

from sagewai.core.base import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Thread-safe registry for in-process agent discovery and delegation.

    Agents register with a list of capability tags. Orchestrators can then
    discover agents by capability and delegate tasks to them.
    """

    _instance: AgentRegistry | None = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._agents: dict[str, _AgentEntry] = {}

    @classmethod
    def get_instance(cls) -> AgentRegistry:
        """Get or create the global singleton registry."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (useful for testing)."""
        with cls._lock:
            cls._instance = None

    def register(
        self,
        agent: BaseAgent,
        *,
        capabilities: list[str] | None = None,
    ) -> None:
        """Register an agent with its capabilities.

        Args:
            agent: The agent instance to register.
            capabilities: List of capability tags (e.g. ["research", "search"]).
                Defaults to extracting from agent's tool names.
        """
        caps = capabilities or [t.name for t in agent.config.tools]
        name = agent.config.name
        self._agents[name] = _AgentEntry(agent=agent, capabilities=caps)
        logger.info("Registered agent %r with capabilities %s", name, caps)

    def unregister(self, name: str) -> None:
        """Remove an agent from the registry by name."""
        self._agents.pop(name, None)

    def discover(self, capability: str) -> list[BaseAgent]:
        """Find all agents that have a given capability.

        Args:
            capability: The capability tag to search for.

        Returns:
            List of agents matching the capability (may be empty).
        """
        return [entry.agent for entry in self._agents.values() if capability in entry.capabilities]

    def get(self, name: str) -> BaseAgent | None:
        """Get a specific agent by name."""
        entry = self._agents.get(name)
        return entry.agent if entry else None

    def list_agents(self) -> dict[str, list[str]]:
        """List all registered agents and their capabilities.

        Returns:
            Dict mapping agent names to their capability lists.
        """
        return {name: entry.capabilities for name, entry in self._agents.items()}

    async def delegate(
        self,
        capability: str,
        message: str,
        *,
        agent_name: str | None = None,
    ) -> str:
        """Delegate a task to the first agent matching a capability.

        Args:
            capability: The capability to match.
            message: The task message to send.
            agent_name: If provided, delegate to this specific agent
                (must have the capability).

        Returns:
            The agent's response string.

        Raises:
            ValueError: If no agent matches the capability.
        """
        if agent_name:
            entry = self._agents.get(agent_name)
            if not entry or capability not in entry.capabilities:
                raise ValueError(
                    f"Agent {agent_name!r} not found or lacks capability {capability!r}"
                )
            agent = entry.agent
        else:
            agents = self.discover(capability)
            if not agents:
                raise ValueError(f"No agent found with capability {capability!r}")
            agent = agents[0]

        logger.info("Delegating %r task to agent %r", capability, agent.config.name)
        return await agent.chat(message)

    def clear(self) -> None:
        """Remove all registered agents."""
        self._agents.clear()

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: str) -> bool:
        return name in self._agents


class _AgentEntry:
    """Internal storage for a registered agent and its capabilities."""

    __slots__ = ("agent", "capabilities")

    def __init__(self, agent: BaseAgent, capabilities: list[str]) -> None:
        self.agent = agent
        self.capabilities = capabilities
