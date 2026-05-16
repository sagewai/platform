# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Templated markdown brief for the ``/explain`` endpoint.

``render_brief`` is a pure function — no LLM calls.  v1.0 is entirely
template-driven from the blueprint metadata already present in the
:class:`~sagewai.admin.autopilot_routes.AutopilotMissionDetail` dict.
"""

from __future__ import annotations

from typing import Any


def render_brief(detail: dict[str, Any]) -> dict[str, Any]:
    """Return a markdown brief and its constituent sections.

    Parameters
    ----------
    detail:
        The ``model_dump()`` output of an
        :class:`~sagewai.admin.autopilot_routes.AutopilotMissionDetail`.

    Returns
    -------
    dict
        ``{"markdown": str, "sections": dict[str, str]}`` where
        ``sections`` has keys ``what_it_does``, ``resources``,
        ``how_to_run``, ``how_to_debug``.
    """
    nodes = (detail.get("agent_graph_json") or {}).get("nodes", []) or []
    tools = detail.get("tools_required") or []
    providers = detail.get("providers_required") or []
    description = detail.get("description") or "No description available."

    what_it_does_lines = [description, "", "**Agents involved:**"]
    if not nodes:
        what_it_does_lines.append("- (no agents declared)")
    for node in nodes:
        role = node.get("role", "agent")
        kind = node.get("kind", "llm")
        node_tools = ", ".join(node.get("tools", [])) or "no tools"
        what_it_does_lines.append(f"- **{role}** ({kind}) — uses {node_tools}")

    resources_lines = ["**Tools required:**"]
    if not tools:
        resources_lines.append("- (none)")
    for t in tools:
        name = t.get("name") if isinstance(t, dict) else str(t)
        desc = t.get("description", "") if isinstance(t, dict) else ""
        resources_lines.append(f"- `{name}` — {desc}" if desc else f"- `{name}`")
    resources_lines.append("")
    resources_lines.append("**Providers required:**")
    if not providers:
        resources_lines.append("- (none)")
    for p in providers:
        name = p.get("name") if isinstance(p, dict) else str(p)
        tier = p.get("tier", "") if isinstance(p, dict) else ""
        resources_lines.append(f"- `{name}` ({tier})" if tier else f"- `{name}`")
    resources_lines.append("")
    resources_lines.append(
        "_Worker pool, sandbox tier, and credential profile assignments "
        "are shown in their respective panels (Plans I/J/K)._"
    )

    how_to_run = (
        "Click the **Run mission** button at the top of this page. The "
        "live trace will replace this brief once execution begins.\n\n"
        "_Run wiring lands in Plan H — the button is disabled in this build._"
    )
    how_to_debug = (
        "If the run fails, the live trace shows the last successful step "
        "and the error message. You can inspect each agent's tool calls, "
        "LLM calls, and outputs inline.\n\n"
        "Re-run with edited slot values from the **Slots** panel above."
    )

    sections = {
        "what_it_does": "\n".join(what_it_does_lines),
        "resources": "\n".join(resources_lines),
        "how_to_run": how_to_run,
        "how_to_debug": how_to_debug,
    }

    markdown = "\n\n".join(
        [
            "## What this will do",
            sections["what_it_does"],
            "## Resources allocated",
            sections["resources"],
            "## How to run",
            sections["how_to_run"],
            "## How to debug",
            sections["how_to_debug"],
        ]
    )
    return {"markdown": markdown, "sections": sections}
