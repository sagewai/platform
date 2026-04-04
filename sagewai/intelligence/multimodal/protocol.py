# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Multimodal processing protocols — transcription and vision."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Transcriber(Protocol):
    """Transcribe audio files to text.

    Implementations must provide an async ``transcribe`` method that accepts
    a file path and optional language hint, returning the transcribed text.
    """

    async def transcribe(self, file_path: str, language: str | None = None) -> str:
        """Transcribe an audio file to text.

        Args:
            file_path: Path to the audio file on disk.
            language: Optional ISO 639-1 language code (e.g. ``"en"``, ``"de"``).

        Returns:
            Transcribed text content.
        """
        ...


@runtime_checkable
class VisionDescriber(Protocol):
    """Generate text descriptions of images.

    Implementations must provide an async ``describe`` method that accepts
    an image path and optional prompt, returning a text description.
    """

    async def describe(self, image_path: str, prompt: str | None = None) -> str:
        """Describe an image in natural language.

        Args:
            image_path: Path to the image file on disk.
            prompt: Optional prompt to guide the description.

        Returns:
            Text description of the image content.
        """
        ...
