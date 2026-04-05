# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Multimodal content parts for ChatMessage.

Defines ``ContentPart`` — a single piece of content in a multimodal message.
A message can contain multiple parts (text, images, audio, video) that are
converted to the appropriate provider format by each engine.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ContentType(str, Enum):
    """Type of content in a message part."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class ContentPart(BaseModel):
    """A single content part in a multimodal message.

    For TEXT parts, populate ``text``.
    For media parts (IMAGE/AUDIO/VIDEO), populate either ``media_url``
    (remote) or ``media_base64`` (inline), plus ``mime_type``.
    """

    type: ContentType
    text: str | None = None
    media_url: str | None = None
    media_base64: str | None = None
    mime_type: str | None = None
    alt_text: str | None = None

    @property
    def is_text(self) -> bool:
        """Return True if this part is a text part."""
        return self.type == ContentType.TEXT

    @property
    def is_media(self) -> bool:
        """Return True if this part contains media (image, audio, or video)."""
        return self.type != ContentType.TEXT
