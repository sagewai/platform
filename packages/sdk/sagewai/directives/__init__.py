# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Sagewai Directive Engine — prompt preprocessing for any LLM.

Resolves ``@context``, ``@memory``, ``@agent``, ``/tool``, ``/mcp``, and
``#meta`` directives (plus ``{{ }}`` templates) into enriched context before
the LLM call. Enables small/local models to leverage Sagewai's full
infrastructure without native tool-calling support.

Quick start::

    from sagewai.directives import DirectiveEngine

    engine = DirectiveEngine(context=my_context_engine, model="codellama:7b")
    result = await engine.resolve("@context('ML basics') Help me learn")
    # result.prompt → enriched text with context injected
"""

from sagewai.directives.ast import (
    ContextBlock,
    DirectiveMetadata,
    DirectiveType,
    ExecutionOverrides,
)
from sagewai.directives.engine import DirectiveEngine, DirectiveResult
from sagewai.directives.file_loader import InstructionFileLoader
from sagewai.directives.formatter import parse_tool_call_from_output
from sagewai.directives.parser import DirectiveParseError
from sagewai.directives.profiles import (
    LARGE,
    MEDIUM,
    SMALL,
    ModelProfile,
    detect_profile,
    get_profile,
)
from sagewai.directives.registry import DirectiveRegistry
from sagewai.directives.tokenizer import has_directives

__all__ = [
    "ContextBlock",
    "DirectiveEngine",
    "DirectiveMetadata",
    "DirectiveParseError",
    "DirectiveRegistry",
    "DirectiveResult",
    "DirectiveType",
    "InstructionFileLoader",
    "ExecutionOverrides",
    "LARGE",
    "MEDIUM",
    "ModelProfile",
    "SMALL",
    "detect_profile",
    "get_profile",
    "has_directives",
    "parse_tool_call_from_output",
]
