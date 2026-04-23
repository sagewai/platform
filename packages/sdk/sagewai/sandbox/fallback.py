# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Mode fallback policy when a backend is unhealthy."""
from __future__ import annotations

import logging

from sagewai.sandbox.models import BackendHealth, SandboxMode

logger = logging.getLogger(__name__)

_DOWNGRADE: dict[SandboxMode, SandboxMode] = {
    SandboxMode.PER_WORKER: SandboxMode.PER_RUN,
    SandboxMode.PER_RUN: SandboxMode.PER_TOOL,
    SandboxMode.PER_TOOL: SandboxMode.NONE,
    SandboxMode.NONE: SandboxMode.NONE,
}


def apply_fallback(
    requested: SandboxMode, health: BackendHealth, *, production: bool
) -> SandboxMode:
    """Return the effective mode after fallback.

    - healthy backend → returns `requested` unchanged
    - unhealthy + non-production → downgrades one step, logs WARN
    - unhealthy + production → raises RuntimeError (never silently downgrade prod)
    """
    if health.ok:
        return requested
    if production:
        raise RuntimeError(
            f"sandbox backend unhealthy in production: {health.detail} "
            f"(requested mode={requested.value}); refusing to downgrade"
        )
    downgraded = _DOWNGRADE[requested]
    logger.warning(
        "sandbox backend unhealthy (%s); downgrading %s → %s",
        health.detail,
        requested.value,
        downgraded.value,
    )
    return downgraded
