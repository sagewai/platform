# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Shared resolver for the authenticated actor id used in audit attribution.

Several admin write paths (Sealed revocations, directive approve/deny) record
*who* performed an action. The actor must come from the authenticated request
context — never a hardcoded placeholder and never a caller-supplied request
body (which is spoofable). :func:`actor_id_for` reads the real principal and
works in both tenancy modes, falling back gracefully so it never raises:

* multi-tenant — ``request.state.context.actor.label`` (e.g. ``alice@example.com``
  or ``api-token:CI``), set by ``AuthMiddleware`` from the session/token.
* single-org — ``request.state.principal.actor_label`` (the admin email), or
  ``request.state.context.actor.label`` if a context was threaded.
* unwired (test apps / no auth middleware) — ``"admin"``.
"""
from __future__ import annotations

from typing import Any

_FALLBACK_ACTOR = "admin"


def actor_id_for(request: Any) -> str:
    """Return the authenticated actor label for audit attribution.

    Order of preference: tenancy ``context.actor`` (covers multi-tenant and any
    single-org request that carries a context), then the session ``principal``'s
    ``actor_label``, then a safe ``"admin"`` fallback. Never raises — an absent
    or malformed context degrades to the fallback so callers can use this for a
    rate-limit key or an ``actor_id`` argument without guarding.
    """
    state = getattr(request, "state", None)

    ctx = getattr(state, "context", None)
    if ctx is not None:
        actor = getattr(ctx, "actor", None)
        label = getattr(actor, "label", None) or getattr(actor, "id", None)
        if label:
            return str(label)

    principal = getattr(state, "principal", None)
    if principal is not None:
        label = getattr(principal, "actor_label", None) or getattr(
            principal, "subject_id", None
        )
        if label:
            return str(label)

    return _FALLBACK_ACTOR


__all__ = ["actor_id_for"]
