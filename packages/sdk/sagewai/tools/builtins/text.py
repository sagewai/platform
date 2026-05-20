# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pure-data text utilities. Stdlib only — no external resources."""
from __future__ import annotations

import difflib
import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


async def diff_text(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute a textual diff between two strings.

    Payload:
        a (str): left-hand text
        b (str): right-hand text
        mode (str, optional): "unified" (default), "context", or "ndiff"

    Returns:
        {"diff": str, "equal": bool}
    """
    a = payload["a"]
    b = payload["b"]
    mode = payload.get("mode", "unified")
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    if mode == "unified":
        diff_iter = difflib.unified_diff(a_lines, b_lines, lineterm="")
    elif mode == "context":
        diff_iter = difflib.context_diff(a_lines, b_lines, lineterm="")
    elif mode == "ndiff":
        diff_iter = difflib.ndiff(a_lines, b_lines)
    else:
        raise ValueError(f"unknown mode: {mode!r}")
    return {"diff": "".join(diff_iter), "equal": a == b}


def _substitute(node: Any, values: dict[str, Any]) -> Any:
    if isinstance(node, str):
        return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), node)
    if isinstance(node, dict):
        return {k: _substitute(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, values) for v in node]
    return node


async def structured_write(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape an LLM/agent's output into a structured row by template substitution.

    Payload:
        template (dict): structure with ``{{ key }}`` placeholders
        values (dict): values for the placeholders; missing keys are
            left as-is so the caller can detect them.

    Returns:
        {"output": dict}
    """
    return {"output": _substitute(payload["template"], payload["values"])}
