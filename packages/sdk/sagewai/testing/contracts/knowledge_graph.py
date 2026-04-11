# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Contract fixture for the Knowledge Graph MCP server."""

from sagewai.testing.contract_test import ServerContract, ToolContract

KNOWLEDGE_GRAPH_CONTRACT = ServerContract(
    server_name="knowledge-graph",
    tool_count=8,
    tools={
        "add_entity": ToolContract(
            name="add_entity",
            required_params=["name"],
            optional_params=["entity_type", "properties"],
            param_types={"name": "string", "entity_type": "string", "properties": "object"},
            response_fields=["name", "type", "properties"],
        ),
        "get_entity": ToolContract(
            name="get_entity",
            required_params=["name"],
            param_types={"name": "string"},
            response_fields=["name", "type", "properties"],
        ),
        "list_entities": ToolContract(
            name="list_entities",
            optional_params=["entity_type"],
            param_types={"entity_type": "string"},
        ),
        "delete_entity": ToolContract(
            name="delete_entity",
            required_params=["name"],
            param_types={"name": "string"},
        ),
        "add_relation": ToolContract(
            name="add_relation",
            required_params=["source", "relation", "target"],
            param_types={"source": "string", "relation": "string", "target": "string"},
            response_fields=["source", "relation", "target"],
        ),
        "get_relations": ToolContract(
            name="get_relations",
            required_params=["entity"],
            param_types={"entity": "string"},
        ),
        "search": ToolContract(
            name="search",
            required_params=["query"],
            optional_params=["max_results"],
            param_types={"query": "string", "max_results": "integer"},
        ),
        "get_neighbors": ToolContract(
            name="get_neighbors",
            required_params=["entity"],
            optional_params=["max_depth"],
            param_types={"entity": "string", "max_depth": "integer"},
            response_fields=["entity", "neighbors", "relations"],
        ),
    },
)
