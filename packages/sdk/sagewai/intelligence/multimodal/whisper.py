# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Transcription backends — local faster-whisper and LiteLLM API."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class FasterWhisperTranscriber:
    """Local transcription using faster-whisper (CTranslate2 backend).

    Runs entirely on CPU with ~150MB model, supports 99 languages.
    Requires ``faster-whisper`` to be installed (``pip install sagewai[multimodal]``).

    Args:
        model_size: Whisper model size (``"tiny"``, ``"base"``, ``"small"``,
            ``"medium"``, ``"large-v3"``). Default ``"base"``.
    """

    def __init__(self, model_size: str = "base") -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ImportError(
                "faster-whisper is required for local transcription. "
                "Install with: pip install sagewai[multimodal]"
            )
        self._model = WhisperModel(model_size, compute_type="int8")

    async def transcribe(self, file_path: str, language: str | None = None) -> str:
        """Transcribe an audio file using faster-whisper.

        Args:
            file_path: Path to the audio file.
            language: Optional ISO 639-1 language code.

        Returns:
            Transcribed text content.
        """
        kwargs: dict = {"language": language} if language else {}
        segments, _info = await asyncio.to_thread(
            self._model.transcribe, file_path, **kwargs
        )
        # segments is a generator; consume in thread to avoid blocking
        text_parts = await asyncio.to_thread(
            lambda segs: [seg.text.strip() for seg in segs], segments
        )
        return " ".join(text_parts)


class LiteLLMTranscriber:
    """API-based transcription via LiteLLM (OpenAI Whisper API compatible).

    Requires ``litellm`` and a valid API key for the configured model.

    Args:
        model: Transcription model name. Default ``"whisper-1"``.
    """

    def __init__(self, model: str = "whisper-1") -> None:
        self._model = model

    async def transcribe(self, file_path: str, language: str | None = None) -> str:
        """Transcribe an audio file via the LiteLLM transcription API.

        Args:
            file_path: Path to the audio file.
            language: Optional ISO 639-1 language code.

        Returns:
            Transcribed text content.
        """
        import litellm

        kwargs: dict = {}
        if language:
            kwargs["language"] = language

        with open(file_path, "rb") as f:
            response = await litellm.atranscription(
                model=self._model,
                file=f,
                **kwargs,
            )
        return response.text
