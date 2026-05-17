# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool adapter — exposes the transform capability as a built-in tool.

An agent calls ``transform(operation, content, params)`` mid-loop on any text
it holds — a tool result, accumulated context, its own intermediate output.
Small models reach it through the directive engine's ``/tool`` prose-parser.
The handler runs the same shared :class:`TransformEngine`; no logic lives here.
"""

from __future__ import annotations

from typing import Any

from sagewai.models.tool import ToolSpec
from sagewai.transform.engine import TransformEngine
from sagewai.transform.models import TransformRequest

_DESCRIPTION = (
    "Transform a body of text with a named operation. Use this to compress or "
    "distil text that is too large or too unstructured to use directly. "
    "operation: 'graphify' (extract relational triples into graph memory), "
    "'summarize' (compress to a short summary), or a custom registered op. "
    "content: the text to transform. params: optional operation-specific options "
    "(e.g. {\"max_words\": 200} for summarize)."
)


def transform_tool_spec(
    *,
    transform_engine: TransformEngine | None = None,
    project_id: str | None = None,
) -> ToolSpec:
    """Build the built-in ``transform`` :class:`ToolSpec`.

    Args:
        transform_engine: The :class:`TransformEngine` to run requests on.
            Defaults to one built from :func:`default_registry`.
        project_id: Project scope threaded onto every :class:`TransformRequest`
            so a transform never writes to another project's graph.
    """
    if transform_engine is None:
        from sagewai.transform import default_registry

        transform_engine = TransformEngine(default_registry())

    async def _handler(
        operation: str, content: str, params: dict[str, Any] | None = None
    ) -> str:
        result = await transform_engine.run(
            TransformRequest(
                operation=operation,
                content=content,
                params=params or {},
                project_id=project_id,
            )
        )
        if result.ok:
            return result.output
        # A normal tool-error path — the agent sees it and can react.
        return f"transform failed: {result.error}"

    return ToolSpec(
        name="transform",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "graphify | summarize | <custom registered op>",
                },
                "content": {
                    "type": "string",
                    "description": "The text to transform.",
                },
                "params": {
                    "type": "object",
                    "description": "Optional operation-specific parameters.",
                },
            },
            "required": ["operation", "content"],
        },
        handler=_handler,
    )
