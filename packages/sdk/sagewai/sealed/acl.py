# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Per-CLI Tier-2 secret allowlist filter (Sealed-iii.D).

The ACL maps tool_name → allowed secret_key_names. Behavior knobs
(non-secret env) ALWAYS pass regardless of ACL — operators tune via
the env block of the profile.

Default-permissive: a tool not listed in the ACL sees all secrets.
Empty list per tool means explicit deny-all of that tool's secrets.
"""
from __future__ import annotations


def compute_allowed_env(
    *,
    full_env: dict[str, str],
    secret_keys: set[str],
    acl: dict[str, list[str]],
    tool_name: str,
) -> tuple[dict[str, str], list[str]]:
    """Compute the env subset visible to ``tool_name``.

    Returns ``(filtered_env, sorted_removed_secret_keys)``.

    Pure function — no side effects, deterministic on inputs.
    """
    if tool_name not in acl:
        return dict(full_env), []

    allowed_for_tool = set(acl[tool_name])
    filtered: dict[str, str] = {}
    removed: list[str] = []

    for k, v in full_env.items():
        if k not in secret_keys:
            filtered[k] = v
            continue
        if k in allowed_for_tool:
            filtered[k] = v
        else:
            removed.append(k)

    return filtered, sorted(removed)
