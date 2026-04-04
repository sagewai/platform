# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Gateway data models — access tokens for scoped agent delegation."""

from __future__ import annotations

import time
from enum import Enum

from pydantic import BaseModel, Field


class TokenStatus(str, Enum):
    """Lifecycle state of an access token."""

    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    USED = "used"


class AccessToken(BaseModel):
    """A scoped access token granting external access to an agent."""

    token_id: str
    token_hash: str
    token_suffix: str = ""  # last 4 chars of plaintext — safe to expose for UI masking
    agent_name: str
    grantor_id: str
    scopes: list[str] = Field(default_factory=lambda: ["chat"])
    status: TokenStatus = TokenStatus.ACTIVE
    single_use: bool = False
    expires_at: float
    used_at: float | None = None
    created_at: float = Field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def is_usable(self) -> bool:
        return (
            self.status == TokenStatus.ACTIVE
            and not self.is_expired
            and not (self.single_use and self.used_at is not None)
        )
