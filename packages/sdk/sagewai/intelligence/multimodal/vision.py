# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Vision description backends — LLM-based and stub fallback."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_MIME_BY_EXT: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


def _detect_image_mime(path: str) -> str:
    """Detect image MIME type from file extension."""
    ext = os.path.splitext(path)[1].lower()
    return _MIME_BY_EXT.get(ext, "image/png")


class LLMVisionDescriber:
    """Describe images using an LLM with vision capabilities.

    Supports any vision-capable model accessible via LiteLLM
    (GPT-4o, GPT-4o-mini, Claude 3, Gemini Pro Vision, etc.).

    Args:
        model: LLM model name with vision support. Default ``"gpt-4o-mini"``.
        max_tokens: Maximum tokens for the description. Default ``500``.
    """

    def __init__(self, model: str = "gpt-4o-mini", max_tokens: int = 500) -> None:
        self._model = model
        self._max_tokens = max_tokens

    async def describe(self, image_path: str, prompt: str | None = None) -> str:
        """Describe an image using a vision-capable LLM.

        Args:
            image_path: Path to the image file on disk.
            prompt: Optional prompt to guide the description.

        Returns:
            Text description of the image content.
        """
        import base64

        import litellm

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        mime = _detect_image_mime(image_path)

        msg_content = [
            {"type": "text", "text": prompt or "Describe this image in detail."},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            },
        ]

        response = await litellm.acompletion(
            model=self._model,
            messages=[{"role": "user", "content": msg_content}],
            max_tokens=self._max_tokens,
        )
        return response.choices[0].message.content


class StubVisionDescriber:
    """Fallback vision describer that returns a placeholder description.

    Useful for testing, offline mode, or when no vision backend is available.
    """

    async def describe(self, image_path: str, prompt: str | None = None) -> str:
        """Return a placeholder description with the image filename.

        Args:
            image_path: Path to the image file on disk.
            prompt: Ignored by the stub.

        Returns:
            Placeholder text containing the image filename.
        """
        name = os.path.basename(image_path)
        return f"[Image: {name}]"
