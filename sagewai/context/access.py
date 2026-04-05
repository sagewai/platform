# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Scope-based access control for the context engine.

Rules:
- ``org`` scope: admin role required for writes
- ``project`` scope: project member can write
- All scopes readable by agents at query time per inheritance rules
"""

from __future__ import annotations

import logging
from typing import Any

from sagewai.context.models import ContextScope

logger = logging.getLogger(__name__)


class AccessDeniedError(Exception):
    """Raised when a user lacks permission for a context operation."""


def check_write_access(
    scope: ContextScope,
    scope_id: str,
    *,
    user_id: str | None = None,
    user_role: str = "member",
    is_programmatic: bool = False,
) -> None:
    """Check if the caller has write access to the given scope.

    Raises ``AccessDeniedError`` if access is denied.

    Parameters
    ----------
    scope:
        The context scope being written to.
    scope_id:
        The scope identifier (org_id or project_id).
    user_id:
        The authenticated user's ID (None for unauthenticated).
    user_role:
        The user's role — ``"admin"`` or ``"member"``.
    is_programmatic:
        True if the write is from an agent (not a human user).
    """
    if scope == ContextScope.ORG:
        if user_role != "admin":
            raise AccessDeniedError(
                f"Organization-level writes require admin role (got {user_role})"
            )

    elif scope == ContextScope.PROJECT:
        # Any authenticated project member can write
        if user_id is None and not is_programmatic:
            raise AccessDeniedError("Project-level writes require authentication")


def check_read_access(
    scope: ContextScope,
    scope_id: str,
    *,
    user_id: str | None = None,
    user_role: str = "member",
    is_agent: bool = False,
) -> None:
    """Check if the caller has read access to the given scope.

    Agents can read all applicable scopes per inheritance rules.
    Users can read any scope they belong to.

    Raises ``AccessDeniedError`` if access is denied.
    """
    # Agents can read everything in their inheritance chain
    if is_agent:
        return

    # Admins can read everything
    if user_role == "admin":
        return

    # org and project scopes are readable by any authenticated user
    if user_id is None:
        raise AccessDeniedError("Reading context requires authentication")
