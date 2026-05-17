# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Data shapes for the transform capability."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TransformRequest(BaseModel):
    """A single transform invocation."""

    operation: str
    content: str
    params: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None


class TransformResult(BaseModel):
    """The outcome of a transform operation."""

    operation: str
    output: str
    ok: bool
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
