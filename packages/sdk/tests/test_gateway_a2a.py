# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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
