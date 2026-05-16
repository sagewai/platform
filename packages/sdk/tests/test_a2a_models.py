# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for A2A Agent Card models."""

from __future__ import annotations

import json

from sagewai.models.agent import AgentConfig
from sagewai.models.tool import ToolSpec
from sagewai.protocols.a2a.models import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
)

# ------------------------------------------------------------------
# AgentCard model tests
# ------------------------------------------------------------------


def test_agent_card_minimal():
    """AgentCard with just a name."""
    card = AgentCard(name="test-agent")
    assert card.name == "test-agent"
    assert card.description == ""
    assert card.version == "1.0.0"
    assert card.skills == []
    assert card.default_input_modes == ["text/plain"]
    assert card.default_output_modes == ["text/plain"]


def test_agent_card_full():
    """AgentCard with all fields populated."""
    card = AgentCard(
        name="nexus-orchestrator",
        description="Plans digital organizations",
        url="https://nexus.example.com/agent",
        version="2.0.0",
        provider=AgentProvider(organization="Sagecurator", url="https://sagecurator.com"),
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        skills=[
            AgentSkill(
                id="create_org",
                name="create_org",
                description="Create organization plans",
                tags=["planning", "org"],
            )
        ],
        security_schemes={"bearer": {"type": "http", "scheme": "bearer"}},
        security=[{"bearer": []}],
    )
    assert card.name == "nexus-orchestrator"
    assert card.provider.organization == "Sagecurator"
    assert len(card.skills) == 1
    assert card.capabilities.streaming is True
    assert card.capabilities.push_notifications is True


def test_agent_card_json_serialization():
    """AgentCard serializes to JSON with camelCase aliases."""
    card = AgentCard(
        name="test",
        capabilities=AgentCapabilities(push_notifications=True),
        default_input_modes=["text/plain", "application/json"],
    )
    data = json.loads(card.model_dump_json(by_alias=True))
    assert "defaultInputModes" in data
    assert "defaultOutputModes" in data
    assert data["capabilities"]["pushNotifications"] is True


def test_agent_card_json_round_trip():
    """AgentCard survives JSON serialization round-trip."""
    card = AgentCard(
        name="round-trip",
        description="Test agent",
        skills=[AgentSkill(id="s1", name="skill1", description="A skill")],
    )
    json_str = card.model_dump_json(by_alias=True)
    restored = AgentCard.model_validate_json(json_str)
    assert restored.name == card.name
    assert len(restored.skills) == 1
    assert restored.skills[0].id == "s1"


# ------------------------------------------------------------------
# AgentCapabilities tests
# ------------------------------------------------------------------


def test_capabilities_defaults():
    """AgentCapabilities defaults are all False."""
    caps = AgentCapabilities()
    assert caps.streaming is False
    assert caps.push_notifications is False
    assert caps.state_transition_history is False


def test_capabilities_camel_case():
    """AgentCapabilities uses camelCase aliases."""
    caps = AgentCapabilities(push_notifications=True, state_transition_history=True)
    data = json.loads(caps.model_dump_json(by_alias=True))
    assert data["pushNotifications"] is True
    assert data["stateTransitionHistory"] is True


# ------------------------------------------------------------------
# AgentSkill tests
# ------------------------------------------------------------------


def test_skill_minimal():
    """AgentSkill with required fields only."""
    skill = AgentSkill(id="search", name="search")
    assert skill.id == "search"
    assert skill.description == ""
    assert skill.tags == []


def test_skill_with_tags_and_examples():
    """AgentSkill with optional fields."""
    skill = AgentSkill(
        id="draft",
        name="draft_post",
        description="Draft a social media post",
        tags=["content", "social"],
        examples=["Draft a LinkedIn post about AI"],
    )
    assert len(skill.tags) == 2
    assert len(skill.examples) == 1


# ------------------------------------------------------------------
# from_config factory method
# ------------------------------------------------------------------


def test_from_config_basic():
    """from_config creates AgentCard from AgentConfig."""
    config = AgentConfig(
        name="test-agent",
        model="gpt-4o",
        system_prompt="You are a helpful assistant.\nMore details here.",
    )
    card = AgentCard.from_config(config)
    assert card.name == "test-agent"
    assert card.description == "You are a helpful assistant."
    assert card.capabilities.streaming is True


def test_from_config_with_tools():
    """from_config maps tools to skills."""
    config = AgentConfig(
        name="tool-agent",
        tools=[
            ToolSpec(
                name="search_web",
                description="Search the web for information",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="draft_email",
                description="Draft an email",
                parameters={"type": "object", "properties": {}},
            ),
        ],
    )
    card = AgentCard.from_config(config)
    assert len(card.skills) == 2
    assert card.skills[0].id == "search_web"
    assert card.skills[0].description == "Search the web for information"
    assert card.skills[1].id == "draft_email"


def test_from_config_custom_description():
    """from_config uses explicit description over system_prompt."""
    config = AgentConfig(
        name="agent",
        system_prompt="System prompt text",
    )
    card = AgentCard.from_config(config, description="Custom description")
    assert card.description == "Custom description"


def test_from_config_with_provider():
    """from_config passes through provider and url."""
    config = AgentConfig(name="agent")
    provider = AgentProvider(organization="Sagecurator", url="https://sagecurator.com")
    card = AgentCard.from_config(
        config,
        url="https://api.example.com/agent",
        version="2.0.0",
        provider=provider,
    )
    assert card.url == "https://api.example.com/agent"
    assert card.version == "2.0.0"
    assert card.provider.organization == "Sagecurator"


def test_from_config_empty_system_prompt():
    """from_config handles empty system_prompt gracefully."""
    config = AgentConfig(name="agent", system_prompt="")
    card = AgentCard.from_config(config)
    assert card.description == ""


def test_from_config_no_tools():
    """from_config returns empty skills when no tools."""
    config = AgentConfig(name="agent")
    card = AgentCard.from_config(config)
    assert card.skills == []
