# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Vault startup audit helper.

Lives in its own module so the main backend stays focused on the
ProfileBackend Protocol. Caller is responsible for invoking this
once per worker startup (typically from admin/serve.py app startup).
"""
from __future__ import annotations

from typing import Any

from sagewai.sealed.audit import AuditWriter


async def emit_startup_authenticated(
    *,
    audit_writer: AuditWriter,
    addr: str,
    namespace: str | None,
    auth_method: str,
    request_id: str | None = None,
) -> None:
    details: dict[str, Any] = {
        "addr": addr,
        "namespace": namespace,
        "auth_method": auth_method,
    }
    if request_id:
        details["vault_request_id"] = request_id
    await audit_writer.emit(
        event_type="vault.startup.authenticated",
        details=details,
    )


async def emit_token_in_state_warning(
    *, audit_writer: AuditWriter,
) -> None:
    """Emitted when an operator pastes a literal token into admin-state."""
    await audit_writer.emit(
        event_type="vault.config.token_in_state",
        details={"recommendation": "use auth_config.token_env instead"},
    )
