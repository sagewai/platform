# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Parameter resolution for backtick-delimited directive arguments.

Backtick-delimited arguments (e.g., ``@agent:name(`query with @datetime`)``)
are preprocessed BEFORE directive tokenization. Built-in variables like
``@datetime``, ``@date``, ``@time``, ``@user``, and ``@project`` are replaced
with their runtime values, and the backticks are converted to single quotes
so the standard tokenizer processes them normally.

This module is called as Phase 0 of ``DirectiveEngine.resolve()``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

_PARAM_RE = re.compile(r"@(datetime|date|time|user|project)\b")

# Match backtick-delimited strings (with escaped backticks support)
_BACKTICK_RE = re.compile(r"`([^`\\]*(?:\\.[^`\\]*)*)`")


def resolve_parameters(text: str, user_id: str | None = None) -> str:
    """Replace @variable references with their runtime values.

    This is intended to be called on the inner content of backtick-delimited
    arguments, not on the full prompt text (to avoid colliding with directive
    sigils like ``@context``).

    Parameters
    ----------
    text:
        Text that may contain ``@datetime``, ``@date``, ``@time``,
        ``@user``, ``@project`` references.
    user_id:
        Current user ID for ``@user`` resolution. Defaults to ``"unknown"``.

    Returns
    -------
    str
        Text with all recognized parameters replaced with runtime values.
    """

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        now = datetime.now(timezone.utc)
        if name == "datetime":
            return now.isoformat()
        elif name == "date":
            return now.strftime("%Y-%m-%d")
        elif name == "time":
            return now.strftime("%H:%M:%S")
        elif name == "user":
            return user_id or "unknown"
        elif name == "project":
            from sagewai.core.context import get_current_project

            ctx = get_current_project()
            return ctx.project_id if ctx else "default"
        return m.group(0)

    return _PARAM_RE.sub(_replace, text)


def resolve_backtick_params(text: str, user_id: str | None = None) -> str:
    """Find backtick-delimited args in text, resolve parameters, convert to single-quoted.

    This transforms::

        @agent:data-extraction(`What is the age of Alice Munro on @datetime`)

    into::

        @agent:data-extraction('What is the age of Alice Munro on 2026-03-28T14:30:00+00:00')

    so the standard tokenizer and parser can process it.

    Parameters
    ----------
    text:
        Full prompt text that may contain backtick-delimited arguments.
    user_id:
        Current user ID for ``@user`` resolution.

    Returns
    -------
    str
        Text with backtick arguments resolved and converted to single quotes.
    """

    def _replace_backtick(m: re.Match) -> str:
        inner = m.group(1)
        resolved = resolve_parameters(inner, user_id)
        # Escape single quotes in the resolved value
        escaped = resolved.replace("'", "\\'")
        return f"'{escaped}'"

    return _BACKTICK_RE.sub(_replace_backtick, text)
