import pytest
from unittest.mock import MagicMock
from sagewai.gateway.a2a import create_agent_card_endpoint


def test_agent_card_generated():
    """Agent card should be auto-generated from agent metadata."""
    mock_agent = MagicMock()
    mock_agent.config.name = "support-agent"
    mock_agent.config.tools = []
    mock_agent.config.model = "gpt-4o"

    card = create_agent_card_endpoint(
        agent=mock_agent,
        url="http://localhost:8000",
        description="Support agent",
    )
    assert card["name"] == "support-agent"
    assert "url" in card
