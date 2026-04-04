# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""A2A (Agent-to-Agent) protocol — models, server, and client.

Implements the A2A protocol specification for agent discovery,
capability advertisement, and task delegation via JSON-RPC.
"""

from sagewai.protocols.a2a.client import A2AClient
from sagewai.protocols.a2a.models import AgentCapabilities, AgentCard, AgentSkill
from sagewai.protocols.a2a.server import A2AServer

__all__ = ["A2AClient", "A2AServer", "AgentCard", "AgentCapabilities", "AgentSkill"]
