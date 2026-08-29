# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Internal persistence key for explicit nullable project scope."""

from __future__ import annotations


def project_scope_key(project_id: str | None) -> str:
    """Derive the non-null database key without exposing it to callers."""
    if project_id is None:
        return "g:"
    if not project_id:
        raise ValueError("project_id must be a non-empty slug or None")
    return f"p:{project_id}"
