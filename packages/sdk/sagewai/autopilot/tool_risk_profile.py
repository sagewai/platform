# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool risk profiles for autopilot sandbox tier assignment.

Each tool name maps to a :class:`SandboxTier`. Unknown tools default to
``UNTRUSTED`` (fail-secure). Tier override requests are validated by
:func:`is_downgrade` — only downgrades (moves to a less-restrictive tier)
are accepted, matching the operator-intent model.

Tier ordering: TRUSTED (0) < SANDBOXED (1) < UNTRUSTED (2)
"""

from __future__ import annotations

from enum import IntEnum


class SandboxTier(IntEnum):
    """Ordered sandbox execution tiers.

    Lower values are less restrictive; higher values are more restrictive.
    """

    TRUSTED = 0
    SANDBOXED = 1
    UNTRUSTED = 2


# ── Default risk registry ─────────────────────────────────────────────────

_TOOL_TIERS: dict[str, SandboxTier] = {
    # TRUSTED — read-only, no network, no side effects
    "read_file": SandboxTier.TRUSTED,
    "list_dir": SandboxTier.TRUSTED,
    "get_time": SandboxTier.TRUSTED,
    "math_eval": SandboxTier.TRUSTED,
    "json_parse": SandboxTier.TRUSTED,
    "regex_match": SandboxTier.TRUSTED,
    # SANDBOXED — outbound network, external APIs, limited side effects
    "web_search": SandboxTier.SANDBOXED,
    "fetch_url": SandboxTier.SANDBOXED,
    "pdf_parse": SandboxTier.SANDBOXED,
    "html_scrape": SandboxTier.SANDBOXED,
    "send_email": SandboxTier.SANDBOXED,
    "slack_post": SandboxTier.SANDBOXED,
    "github_read": SandboxTier.SANDBOXED,
    "db_query_ro": SandboxTier.SANDBOXED,
    # UNTRUSTED — arbitrary execution, write access, privileged operations
    "shell_exec": SandboxTier.UNTRUSTED,
    "code_exec": SandboxTier.UNTRUSTED,
    "write_file": SandboxTier.UNTRUSTED,
    "db_write": SandboxTier.UNTRUSTED,
    "docker_run": SandboxTier.UNTRUSTED,
    "kubectl_apply": SandboxTier.UNTRUSTED,
    "github_write": SandboxTier.UNTRUSTED,
}


def get_tier(tool_name: str) -> SandboxTier:
    """Return the sandbox tier for *tool_name*.

    Unknown tools default to ``SandboxTier.UNTRUSTED`` (fail-secure).
    """
    return _TOOL_TIERS.get(tool_name, SandboxTier.UNTRUSTED)


def is_downgrade(proposed: SandboxTier, current: SandboxTier) -> bool:
    """Return ``True`` if *proposed* reduces the trust level vs *current*.

    A "downgrade" moves to a lower-trust (more restrictive) tier, e.g.
    TRUSTED → SANDBOXED or SANDBOXED → UNTRUSTED. Operators may only
    apply overrides that are downgrades; upgrades (more permissive) are
    rejected by the admin route.
    """
    return proposed > current


def tier_for_tools(tool_names: list[str]) -> SandboxTier:
    """Return the most restrictive tier required by any tool in *tool_names*.

    An empty list is fully TRUSTED. Unknown tools pull the result to UNTRUSTED.
    """
    if not tool_names:
        return SandboxTier.TRUSTED
    return max(get_tier(t) for t in tool_names)
