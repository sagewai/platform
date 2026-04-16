# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Safe quality-filter expression evaluator.

Grammar (case-sensitive keywords)::

    expr        := conjunction (OR conjunction)*
    conjunction := atom (AND atom)*
    atom        := IDENT op VALUE
                 | IDENT "is" ("None" | "not None" | "True" | "False")

    op    := ">=" | "<=" | ">" | "<" | "==" | "!="
    VALUE := NUMBER | "None" | "True" | "False"
    IDENT := [a-z_][a-z0-9_]*

No ``eval()`` or ``exec()`` is used. Missing context keys cause the
atom to evaluate to ``False``. Malformed expressions raise
:exc:`FilterParseError`.
"""

from __future__ import annotations

import re
from typing import Any


class FilterParseError(ValueError):
    """Raised when a quality-filter expression cannot be parsed."""


# ── Tokeniser ──────────────────────────────────────────────────────

_TOKEN_RE = re.compile(
    r"""
    (?P<OP>>=|<=|!=|>|<|==)
    |(?P<KW>AND|OR|is\s+not|is)
    |(?P<IDENT>[a-z_][a-z0-9_]*)
    |(?P<NUM>-?\d+(?:\.\d+)?)
    |(?P<LIT>None|True|False|not\s+None)
    """,
    re.VERBOSE,
)


def _tokenise(expr: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    s = expr.strip()
    while pos < len(s):
        # skip whitespace
        while pos < len(s) and s[pos] in " \t":
            pos += 1
        if pos >= len(s):
            break
        m = _TOKEN_RE.match(s, pos)
        if m is None:
            raise FilterParseError(f"Unexpected character at position {pos!r} in {expr!r}")
        tok = m.group().strip()
        tokens.append(tok)
        pos = m.end()
    return tokens


# ── Parser / evaluator ─────────────────────────────────────────────


def _coerce(raw: str) -> Any:
    if raw == "None":
        return None
    if raw == "True":
        return True
    if raw == "False":
        return False
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        raise FilterParseError(f"Cannot coerce value token {raw!r}")


_OP_FN: dict[str, Any] = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


class _Missing:
    """Sentinel for missing context keys."""


_MISSING = _Missing()


def _eval_atom(tokens: list[str], pos: int, context: dict[str, Any]) -> tuple[bool, int]:
    """Parse and evaluate one atom. Returns (result, new_pos)."""
    if pos >= len(tokens):
        raise FilterParseError("Unexpected end of expression")

    ident = tokens[pos]
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", ident):
        raise FilterParseError(f"Expected identifier, got {ident!r}")
    pos += 1

    if pos >= len(tokens):
        raise FilterParseError(f"Incomplete atom after identifier {ident!r}")

    next_tok = tokens[pos]

    # --- "is not" as a single token from tokeniser ---
    if next_tok == "is not":
        pos += 1
        if pos >= len(tokens) or tokens[pos] != "None":
            raise FilterParseError("Expected 'None' after 'is not'")
        pos += 1
        val = context.get(ident, _MISSING)
        if isinstance(val, _Missing):
            return False, pos
        return val is not None, pos

    # --- "is" forms ---
    if next_tok == "is":
        pos += 1
        if pos >= len(tokens):
            raise FilterParseError("Expected qualifier after 'is'")
        qual = tokens[pos]
        pos += 1
        val = context.get(ident, _MISSING)
        if isinstance(val, _Missing):
            return False, pos
        if qual == "None":
            return val is None, pos
        if qual == "not":
            # "is not None" — "not" then "None" as separate tokens
            if pos >= len(tokens) or tokens[pos] != "None":
                raise FilterParseError("Expected 'None' after 'is not'")
            pos += 1
            return val is not None, pos
        if qual == "True":
            return val is True, pos
        if qual == "False":
            return val is False, pos
        raise FilterParseError(f"Unknown 'is' qualifier {qual!r}")

    # --- comparison operator forms ---
    if next_tok not in _OP_FN:
        raise FilterParseError(f"Expected operator, got {next_tok!r}")
    op_fn = _OP_FN[next_tok]
    pos += 1

    if pos >= len(tokens):
        raise FilterParseError("Expected value after operator")
    raw_val = tokens[pos]
    pos += 1

    rhs = _coerce(raw_val)
    lhs = context.get(ident, _MISSING)
    if isinstance(lhs, _Missing):
        return False, pos

    try:
        return op_fn(lhs, rhs), pos
    except TypeError:
        return False, pos


def _eval_conjunction(tokens: list[str], pos: int, context: dict[str, Any]) -> tuple[bool, int]:
    """Parse one conjunction (AND chain). Returns (result, new_pos)."""
    result, pos = _eval_atom(tokens, pos, context)
    while pos < len(tokens) and tokens[pos] == "AND":
        pos += 1
        rhs, pos = _eval_atom(tokens, pos, context)
        result = result and rhs
    return result, pos


def _eval_expr(tokens: list[str], context: dict[str, Any]) -> bool:
    """Parse the full expression (OR of conjunctions)."""
    if not tokens:
        raise FilterParseError("Expression must not be empty")
    result, pos = _eval_conjunction(tokens, 0, context)
    while pos < len(tokens) and tokens[pos] == "OR":
        pos += 1
        rhs, pos = _eval_conjunction(tokens, pos, context)
        result = result or rhs
    if pos < len(tokens):
        raise FilterParseError(f"Unexpected token {tokens[pos]!r} at position {pos}")
    return result


def _eval_filter(expr: str, context: dict[str, Any]) -> bool:
    """Evaluate a quality-filter expression string against a context dict.

    Args:
        expr: A non-empty filter expression string.
        context: Key-value mapping of run metadata.

    Returns:
        ``True`` if the expression passes, ``False`` otherwise.

    Raises:
        :exc:`FilterParseError`: If the expression is malformed.
    """
    stripped = expr.strip()
    if not stripped:
        raise FilterParseError("Expression must not be empty")
    tokens = _tokenise(stripped)
    return _eval_expr(tokens, context)


def eval_quality_filter(expr: str | None, context: dict[str, Any]) -> bool:
    """Public wrapper: ``None`` expression always returns ``True`` (no filter).

    Args:
        expr: The ``TrainingHook.quality_filter`` string, or ``None``.
        context: Key-value mapping of run metadata.

    Returns:
        ``True`` when the run qualifies for inclusion in the dataset.
    """
    if expr is None:
        return True
    return _eval_filter(expr, context)
