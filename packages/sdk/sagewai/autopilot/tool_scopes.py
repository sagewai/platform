# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tool-to-Sealed-scope mapping for autopilot agent steps.

Each tool name maps to a set of Sealed scope strings that a matching
profile must grant. Unknown tools return an empty set — they require no
Sealed profile (they carry no privileged credentials).

Scope naming convention: ``<domain>.<resource>.<action>``
Examples: ``network.outbound.fetch``, ``fs.read``, ``exec.shell``
"""

from __future__ import annotations

_TOOL_SCOPES: dict[str, frozenset[str]] = {
    # File-system read-only
    "read_file":   frozenset({"fs.read"}),
    "list_dir":    frozenset({"fs.read"}),
    # Outbound network / external APIs
    "web_search":  frozenset({"network.outbound.fetch"}),
    "fetch_url":   frozenset({"network.outbound.fetch"}),
    "pdf_parse":   frozenset({"network.outbound.fetch"}),
    "html_scrape": frozenset({"network.outbound.fetch"}),
    # Messaging / notifications — need API credentials
    "send_email":  frozenset({"network.outbound.fetch", "secrets.email_api_key"}),
    "slack_post":  frozenset({"network.outbound.fetch", "secrets.slack_token"}),
    # GitHub read
    "github_read": frozenset({"network.outbound.fetch", "secrets.github_token"}),
    # GitHub write
    "github_write": frozenset({"network.outbound.fetch", "secrets.github_token", "git.write"}),
    # Database
    "db_query_ro": frozenset({"db.read", "secrets.db_url"}),
    "db_write":    frozenset({"db.read", "db.write", "secrets.db_url"}),
    # Privileged execution
    "shell_exec":  frozenset({"exec.shell"}),
    "code_exec":   frozenset({"exec.code"}),
    "write_file":  frozenset({"fs.write"}),
    "docker_run":  frozenset({"exec.docker"}),
    "kubectl_apply": frozenset({"exec.kubectl", "secrets.kubeconfig"}),
}


def get_scopes(tool_name: str) -> frozenset[str]:
    """Return the set of Sealed scopes required by *tool_name*.

    Unknown tools return an empty frozenset — they need no Sealed profile.
    """
    return _TOOL_SCOPES.get(tool_name, frozenset())


def scopes_for_tools(tool_names: list[str]) -> frozenset[str]:
    """Return the union of scopes required by all tools in *tool_names*.

    An empty list returns an empty frozenset.  Unknown tools contribute
    nothing to the union (they carry no scope requirements).
    """
    result: set[str] = set()
    for t in tool_names:
        result |= _TOOL_SCOPES.get(t, frozenset())
    return frozenset(result)
