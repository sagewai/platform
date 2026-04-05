# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""A2A protocol gateway — auto-generates agent cards from BaseAgent instances."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent


def create_agent_card_endpoint(
    agent: BaseAgent,
    url: str,
    description: str = "",
    version: str = "1.0.0",
) -> dict[str, Any]:
    """Generate an A2A agent card dict from agent metadata."""
    skills = []
    for tool in agent.config.tools:
        skills.append({
            "id": tool.name,
            "name": tool.name,
            "description": tool.description,
        })

    return {
        "name": agent.config.name,
        "url": url,
        "version": version,
        "description": description or f"Agent: {agent.config.name}",
        "capabilities": {"streaming": True},
        "skills": skills,
    }


def create_a2a_router(agents: dict[str, BaseAgent], base_url: str) -> APIRouter:
    """Create FastAPI router with /.well-known/agent-card.json for each agent."""
    router = APIRouter()

    @router.get("/.well-known/agent-card.json")
    async def agent_card() -> JSONResponse:
        if len(agents) == 1:
            name, agent = next(iter(agents.items()))
            card = create_agent_card_endpoint(agent, base_url)
            return JSONResponse(card)
        cards = [
            create_agent_card_endpoint(agent, base_url)
            for agent in agents.values()
        ]
        return JSONResponse(cards)

    return router
