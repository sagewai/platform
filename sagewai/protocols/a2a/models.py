# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""A2A Agent Card — Pydantic models matching the A2A protocol specification.

An Agent Card is a JSON document that describes an agent's identity,
capabilities, skills, and endpoints, enabling discovery and interoperability
between agents.

See: https://a2a-protocol.org/latest/specification/
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentProvider(BaseModel):
    """Organization or individual that provides the agent."""

    organization: str
    url: str | None = None


class AgentCapabilities(BaseModel):
    """Capabilities advertised by the agent."""

    streaming: bool = False
    push_notifications: bool = Field(default=False, alias="pushNotifications")
    state_transition_history: bool = Field(default=False, alias="stateTransitionHistory")

    model_config = {"populate_by_name": True}


class AgentSkill(BaseModel):
    """A specific skill or capability the agent can perform."""

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class AgentCard(BaseModel):
    """A2A Agent Card — describes an agent for discovery and interoperability.

    Can be auto-generated from an ``AgentConfig`` via ``from_config()``.
    """

    name: str
    description: str = ""
    url: str = ""
    version: str = "1.0.0"
    provider: AgentProvider | None = None
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    skills: list[AgentSkill] = Field(default_factory=list)
    default_input_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        alias="defaultInputModes",
    )
    default_output_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        alias="defaultOutputModes",
    )
    security_schemes: dict[str, Any] = Field(default_factory=dict, alias="securitySchemes")
    security: list[dict[str, list[str]]] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @classmethod
    def from_config(
        cls,
        config: Any,
        *,
        url: str = "",
        version: str = "1.0.0",
        provider: AgentProvider | None = None,
        description: str = "",
    ) -> AgentCard:
        """Create an AgentCard from an AgentConfig.

        Tools are mapped to skills automatically. The agent's system prompt
        is used as description if none is explicitly provided.

        Args:
            config: An ``AgentConfig`` instance.
            url: The agent's endpoint URL.
            version: Version string for the agent.
            provider: Provider information.
            description: Override for agent description. Falls back to
                the first line of ``config.system_prompt`` if empty.
        """
        # Derive description from system prompt if not provided
        if not description and config.system_prompt:
            first_line = config.system_prompt.strip().split("\n")[0]
            description = first_line

        # Map tools to A2A skills
        skills = []
        for tool in config.tools:
            skills.append(
                AgentSkill(
                    id=tool.name,
                    name=tool.name,
                    description=tool.description,
                )
            )

        # Detect capabilities from config
        capabilities = AgentCapabilities(
            streaming=True,  # BaseAgent supports chat_stream
        )

        return cls(
            name=config.name,
            description=description,
            url=url,
            version=version,
            provider=provider,
            capabilities=capabilities,
            skills=skills,
        )
