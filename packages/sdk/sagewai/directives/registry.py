# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Custom directive registration.

Allows developers to register custom directive types that extend the built-in
set. Custom directives are resolved via user-provided handler functions.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from sagewai.directives.ast import ASTNode, ContextBlock, DirectiveType, ResolvedDirective, TextNode


# A directive handler receives the parsed arguments and returns content.
DirectiveHandler = Callable[..., Awaitable[str] | str]


class CustomDirective:
    """Definition of a custom directive type."""

    def __init__(
        self,
        name: str,
        sigil: str,
        handler: DirectiveHandler,
        description: str = "",
        *,
        raw_args: bool = False,
    ) -> None:
        self.name = name
        self.sigil = sigil  # e.g., "@kb" for @kb('query')
        self.handler = handler
        self.description = description
        self.raw_args = raw_args
        # Build regex for this directive
        escaped = re.escape(sigil)
        if raw_args:
            # Multi-argument form: ``@sigil(a, b, kw=v)``. Capture everything
            # between the outer parens, tolerating one level of nested parens
            # (so a nested directive may be passed as an argument). The
            # handler receives the raw, unparsed argument string.
            self.pattern = re.compile(rf"{escaped}\(((?:[^()]|\([^()]*\))*)\)")
        else:
            # Single-argument form: ``@sigil('arg', kw=v)``. The handler
            # receives the first quoted argument.
            _QUOTED = r"""(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")"""
            self.pattern = re.compile(
                rf"{escaped}\(\s*{_QUOTED}(?:\s*,\s*\w+\s*=\s*(?:{_QUOTED}|\d+))*\s*\)"
            )


class DirectiveRegistry:
    """Registry for custom directive types.

    Custom directives extend the built-in set with user-defined handlers::

        registry = DirectiveRegistry()
        registry.register("knowledge_base", "@kb", handler=search_kb)
        # Now @kb('query') works in prompts
    """

    def __init__(self) -> None:
        self._directives: dict[str, CustomDirective] = {}

    def register(
        self,
        name: str,
        sigil: str,
        handler: DirectiveHandler,
        description: str = "",
        *,
        raw_args: bool = False,
    ) -> None:
        """Register a custom directive type.

        Args:
            name: Unique identifier for the directive.
            sigil: The prefix used in prompts (e.g., ``"@kb"``).
            handler: Async or sync function that resolves the directive.
            description: Human-readable description.
            raw_args: When True, the directive accepts a multi-argument call
                ``@sigil(a, b, kw=v)`` and the handler receives the raw,
                unparsed argument string. When False (default), the directive
                uses the single-quoted-argument form ``@sigil('arg')`` and the
                handler receives the first quoted argument.
        """
        self._directives[name] = CustomDirective(
            name=name,
            sigil=sigil,
            handler=handler,
            description=description,
            raw_args=raw_args,
        )

    def unregister(self, name: str) -> None:
        """Remove a custom directive."""
        self._directives.pop(name, None)

    def get(self, name: str) -> CustomDirective | None:
        """Get a custom directive by name."""
        return self._directives.get(name)

    def list_directives(self) -> list[CustomDirective]:
        """List all registered custom directives."""
        return list(self._directives.values())

    def match(self, text: str) -> list[tuple[CustomDirective, re.Match]]:
        """Find all custom directive matches in text."""
        matches = []
        for directive in self._directives.values():
            for m in directive.pattern.finditer(text):
                matches.append((directive, m))
        return matches
