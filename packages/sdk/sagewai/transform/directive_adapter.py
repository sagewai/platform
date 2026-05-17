# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Directive adapter — wires ``@transform`` into the DirectiveEngine.

A thin adapter: it registers a ``@transform`` custom directive on a
:class:`~sagewai.directives.engine.DirectiveEngine` and translates the parsed
directive call into a :class:`~sagewai.transform.models.TransformRequest` run
by a shared :class:`~sagewai.transform.engine.TransformEngine`. No transform
logic lives here.
"""

from __future__ import annotations

import logging
from typing import Any

from sagewai.transform.engine import TransformEngine
from sagewai.transform.models import TransformRequest

logger = logging.getLogger(__name__)

_SIGIL = "@transform"


def register_transform_directive(
    engine: Any, *, transform_engine: TransformEngine | None = None
) -> None:
    """Register a first-class ``@transform`` directive on a DirectiveEngine.

    ``@transform(operation, source, **params)`` is resolved and *executed*
    pre-LLM: ``source`` — a nested directive reference (``@context('...')``,
    ``@memory('...')``) or a quoted literal — is resolved to text, the
    transform runs on that text, and :attr:`TransformResult.output` is
    injected into the enriched prompt in place of the directive.

    A failed transform injects nothing and logs a warning; directive
    resolution already degrades gracefully when a directive yields no content.

    Args:
        engine: The host :class:`DirectiveEngine`.
        transform_engine: The :class:`TransformEngine` to run requests on.
            Defaults to one built from :func:`default_registry`, inheriting
            the host engine's model when it exposes one.
    """
    if transform_engine is None:
        from sagewai.transform import default_registry

        model = getattr(engine, "model", None)
        registry = default_registry(model=model) if model else default_registry()
        transform_engine = TransformEngine(registry)

    async def _handler(raw_args: str) -> str:
        try:
            operation, source_expr, params = _parse_directive_args(raw_args)
        except ValueError as exc:
            logger.warning("@transform: could not parse %r: %s", raw_args, exc)
            return ""

        content = await _resolve_source(engine, source_expr)
        result = await transform_engine.run(
            TransformRequest(operation=operation, content=content, params=params)
        )
        if result.ok:
            return result.output
        logger.warning("@transform(%s) failed: %s", operation, result.error)
        return ""

    engine.register(
        "transform",
        _SIGIL,
        _handler,
        "Transform a body of text (graphify / summarize / custom op)",
        raw_args=True,
    )


async def _resolve_source(engine: Any, source_expr: str) -> str:
    """Resolve the transform ``source`` argument to plain text.

    A nested directive reference (it starts with ``@`` or ``/``) is resolved
    through the host engine; anything else is treated as a quoted/bare literal.

    The transform runs on the *raw* resolved-directive content — not the
    formatted, per-model-compressed ``.prompt`` — so an operation that parses
    its input (a custom op, ``graphify``) sees the real text.
    """
    expr = source_expr.strip()
    if expr[:1] not in ("@", "/"):
        return _unquote(expr)

    from sagewai.directives.ast import MetaNode, TextNode

    resolved = await engine.resolve(expr)
    pieces = [
        r.content
        for r in resolved.directives_found
        if r.content and not isinstance(r.node, (TextNode, MetaNode))
    ]
    return "\n\n".join(pieces) if pieces else resolved.prompt


def _parse_directive_args(raw_args: str) -> tuple[str, str, dict[str, Any]]:
    """Parse ``operation, source, **params`` from a ``@transform`` call."""
    parts = _split_top_level(raw_args)
    if len(parts) < 2:
        raise ValueError("expected at least (operation, source)")
    operation = parts[0].strip()
    if not operation:
        raise ValueError("empty operation")
    source_expr = parts[1].strip()
    params: dict[str, Any] = {}
    for extra in parts[2:]:
        key, sep, val = extra.partition("=")
        if not sep:
            raise ValueError(f"malformed param: {extra!r}")
        params[key.strip()] = _coerce(val.strip())
    return operation, source_expr, params


def _split_top_level(text: str) -> list[str]:
    """Split ``text`` on top-level commas, ignoring quotes and nested parens."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in text:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch in ("(", "["):
            depth += 1
            buf.append(ch)
        elif ch in (")", "]"):
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _unquote(value: str) -> str:
    """Strip a single matching pair of surrounding quotes, if present."""
    if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
        return value[1:-1]
    return value


def _coerce(value: str) -> Any:
    """Coerce a param value: quoted → str, numeric → int/float, else str."""
    if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value
