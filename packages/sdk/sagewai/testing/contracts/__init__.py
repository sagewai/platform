# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""MCP server contract fixtures and testing framework.

Each submodule exports a ``ServerContract`` defining the expected tool schemas
for an MCP server. Used by ``McpContractTest`` to catch schema drift.
"""

from sagewai.testing.contract_test import (
    ContractViolationError,
    McpContractTest,
    ServerContract,
    ToolContract,
)
from sagewai.testing.contracts.admin import ADMIN_CONTRACT
from sagewai.testing.contracts.calendar import CALENDAR_CONTRACT
from sagewai.testing.contracts.commerce import COMMERCE_CONTRACT
from sagewai.testing.contracts.documents import DOCUMENTS_CONTRACT
from sagewai.testing.contracts.email import EMAIL_CONTRACT
from sagewai.testing.contracts.knowledge_graph import KNOWLEDGE_GRAPH_CONTRACT
from sagewai.testing.contracts.payments import PAYMENTS_CONTRACT
from sagewai.testing.contracts.slack import SLACK_CONTRACT
from sagewai.testing.contracts.travel import TRAVEL_CONTRACT

ALL_CONTRACTS = {
    "admin": ADMIN_CONTRACT,
    "calendar": CALENDAR_CONTRACT,
    "commerce": COMMERCE_CONTRACT,
    "documents": DOCUMENTS_CONTRACT,
    "email": EMAIL_CONTRACT,
    "knowledge-graph": KNOWLEDGE_GRAPH_CONTRACT,
    "payments": PAYMENTS_CONTRACT,
    "slack": SLACK_CONTRACT,
    "travel": TRAVEL_CONTRACT,
}

__all__ = [
    "ADMIN_CONTRACT",
    "ALL_CONTRACTS",
    "CALENDAR_CONTRACT",
    "COMMERCE_CONTRACT",
    "CONTRACTS",
    "ContractViolationError",
    "DOCUMENTS_CONTRACT",
    "EMAIL_CONTRACT",
    "KNOWLEDGE_GRAPH_CONTRACT",
    "McpContractTest",
    "PAYMENTS_CONTRACT",
    "SLACK_CONTRACT",
    "ServerContract",
    "TRAVEL_CONTRACT",
    "ToolContract",
]
