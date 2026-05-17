# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The transform capability.

A single primitive that applies a named operation — ``graphify``,
``summarize``, or a custom registered op — to a body of text. It is
exposed two ways: as a first-class ``@transform`` directive (resolved and
executed pre-LLM) and as a built-in ``transform`` tool (called mid-loop by
an agent). Both run on, and are usable by, small/local models.
"""

from __future__ import annotations

from sagewai.transform.directive_adapter import register_transform_directive
from sagewai.transform.engine import TransformEngine
from sagewai.transform.models import TransformRequest, TransformResult
from sagewai.transform.operations import _DEFAULT_MODEL, graphify, summarize
from sagewai.transform.registry import TransformRegistry
from sagewai.transform.tool_adapter import transform_tool_spec

__all__ = [
    "TransformEngine",
    "TransformRegistry",
    "TransformRequest",
    "TransformResult",
    "graphify",
    "summarize",
    "default_registry",
    "register_transform_directive",
    "transform_tool_spec",
]


def default_registry(*, model: str = _DEFAULT_MODEL) -> TransformRegistry:
    """A registry pre-loaded with the built-in ``graphify``/``summarize`` ops.

    ``model`` is the small/cheap model the built-in operations use for their
    LLM calls; callers may override it. The built-ins are registered as thin
    wrappers that thread ``model`` into the operation's params so the
    operation builds its default collaborator (LLM client / relation
    extractor) accordingly.
    """
    registry = TransformRegistry()

    async def _graphify(content, *, project_id=None, **params):
        params.setdefault("model", model)
        return await graphify(content, project_id=project_id, **params)

    async def _summarize(content, *, project_id=None, **params):
        params.setdefault("model", model)
        return await summarize(content, project_id=project_id, **params)

    registry.register("graphify", _graphify)
    registry.register("summarize", _summarize)
    return registry
