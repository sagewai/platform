# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Plan-preview card builder.

:func:`build_preview` assembles a human-readable plain-text "plan card"
from a matched :class:`Blueprint` and a dict of extracted slot values.
The card is shown to the user for approval before the mission starts
(Plan 7 will render a richer UI; this function produces the text that
backs both the CLI and the admin UI).

The output format is intentionally simple and stable so that tests can
make reliable string assertions without depending on whitespace:

    Blueprint : <title>
    Category  : <category>
    Mode      : <mode>
    Description:
      <description>
    Slots:
      <name> = <value>      (or "(unset)" for None)
    Tools required:
      <tool1>, <tool2>
"""

from __future__ import annotations

from typing import Any

from sagewai.autopilot.blueprint import Blueprint

_UNSET = "(unset)"


def build_preview(
    blueprint: Blueprint,
    *,
    slots: dict[str, Any],
) -> str:
    """Render a human-readable plan card for *blueprint* with *slots* filled in.

    Args:
        blueprint: The matched blueprint.
        slots: Extracted slot values.  Keys not in the blueprint are
            included as-is (the preview builder does not validate slots —
            that is ``blueprint.validate_slots``'s job, called later by
            the controller in Plan 4).

    Returns:
        A non-empty plain-text string suitable for CLI output or the
        admin UI plan-preview panel.
    """
    lines: list[str] = [
        f"Blueprint : {blueprint.title}",
        f"Category  : {blueprint.category}",
        f"Mode      : {blueprint.mode.value}",
        "Description:",
        f"  {blueprint.description}",
    ]

    if slots:
        lines.append("Slots:")
        for name, value in slots.items():
            rendered = _UNSET if value is None else str(value)
            lines.append(f"  {name} = {rendered}")

    if blueprint.tools_required:
        lines.append("Tools required:")
        lines.append(f"  {', '.join(blueprint.tools_required)}")

    return "\n".join(lines)
