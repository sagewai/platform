# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pydantic models for Sealed-i profile management."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProfileMetadata(BaseModel):
    """Profile fields safe to expose without unlocking secrets."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)
    last_rotated_at: datetime | None = None
    allowed_workflows: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    secret_keys: list[str] = Field(default_factory=list)
    acl: dict[str, list[str]] = Field(default_factory=dict)


class Profile(ProfileMetadata):
    """Full profile including decrypted secret values."""

    secrets: dict[str, str] = Field(default_factory=dict)


class ProfileWritePayload(BaseModel):
    """Request body for create/update endpoints."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None  # required at create; ignored on update (id from URL)
    name: str = Field(..., min_length=1)
    description: str = ""
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)
    allowed_workflows: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    acl: dict[str, list[str]] = Field(default_factory=dict)


class EffectiveProfile(BaseModel):
    """Resolved cascade — what gets injected into the sandbox."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    env: dict[str, str]
    secret_keys: set[str]
    cascade_origins: dict[str, str]
    acl: dict[str, list[str]] = Field(default_factory=dict)
