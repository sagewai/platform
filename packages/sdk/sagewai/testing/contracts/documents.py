# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Contract fixture for the Documents MCP server."""

from sagewai.testing.contract_test import ServerContract, ToolContract

DOCUMENTS_CONTRACT = ServerContract(
    server_name="documents",
    tool_count=8,
    tools={
        "create_document": ToolContract(
            name="create_document",
            required_params=["title"],
            optional_params=["content", "folder", "tags"],
            param_types={
                "title": "string",
                "content": "string",
                "folder": "string",
                "tags": "array",
            },
            response_fields=["document_id", "title"],
        ),
        "get_document": ToolContract(
            name="get_document",
            required_params=["document_id"],
            param_types={"document_id": "string"},
            response_fields=["document_id", "title"],
        ),
        "update_document": ToolContract(
            name="update_document",
            required_params=["document_id"],
            optional_params=["title", "content", "tags"],
            param_types={
                "document_id": "string",
                "title": "string",
                "content": "string",
                "tags": "array",
            },
        ),
        "delete_document": ToolContract(
            name="delete_document",
            required_params=["document_id"],
            param_types={"document_id": "string"},
        ),
        "list_documents": ToolContract(
            name="list_documents",
            optional_params=["folder", "tag"],
            param_types={"folder": "string", "tag": "string"},
        ),
        "search_documents": ToolContract(
            name="search_documents",
            required_params=["query"],
            param_types={"query": "string"},
        ),
        "create_folder": ToolContract(
            name="create_folder",
            required_params=["name"],
            optional_params=["parent_id"],
            param_types={"name": "string", "parent_id": "string"},
            response_fields=["folder_id", "name"],
        ),
        "list_folders": ToolContract(
            name="list_folders",
        ),
    },
)
