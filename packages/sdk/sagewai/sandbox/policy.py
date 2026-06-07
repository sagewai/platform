# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.

"""Host-execution policy (spec §3, #10B)."""
from __future__ import annotations

import os


def host_exec_allowed() -> bool:
    """Whether host-backed (on-host NullBackend / bash / stdio MCP) execution is permitted.

    Default DENY everywhere; opt in with SAGEWAI_ALLOW_HOST_EXEC=1. This protects
    any deployment (local or container) that is exposed, not just the published image.
    """
    return os.environ.get("SAGEWAI_ALLOW_HOST_EXEC", "") in {"1", "true"}
