# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Durable admin audit — persists to admin-state `audit_events` + structured log."""
from __future__ import annotations

import datetime
import logging
import secrets
from typing import Any

logger = logging.getLogger("sagewai.admin")
_MAX_AUDIT = 1000


def emit_audit(sf, *, event_type: str, actor_label: str,
               target: str = "", details: dict[str, Any] | None = None) -> None:
    """Append a durable audit event to the admin state and emit a structured log."""
    event = {
        "id": f"evt-{secrets.token_hex(6)}",
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event_type": event_type,
        "actor_label": actor_label,
        "target": target,
        "details": details or {},
    }

    def _apply(data: dict[str, Any]) -> None:
        events = data.setdefault("audit_events", [])
        events.append(event)
        data["audit_events"] = events[-_MAX_AUDIT:]

    sf._mutate(_apply)
    logger.info(
        "audit %s by %s", event_type, actor_label,
        extra={"event": f"audit.{event_type}", "actor": actor_label, "target": target},
    )
