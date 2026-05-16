# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for Intelligence Layer Phase I6 — multimodal processing protocols."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from sagewai.intelligence.config import IntelligenceConfig
from sagewai.intelligence.multimodal.protocol import Transcriber, VisionDescriber
from sagewai.intelligence.multimodal.vision import (
    LLMVisionDescriber,
    StubVisionDescriber,
    _detect_image_mime,
)
from sagewai.intelligence.multimodal.whisper import (
    FasterWhisperTranscriber,
    LiteLLMTranscriber,
)
from sagewai.intelligence.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    """Verify that all backends satisfy their runtime_checkable protocols."""

    def test_stub_vision_is_vision_describer(self) -> None:
        describer = StubVisionDescriber()
        assert isinstance(describer, VisionDescriber)

    def test_llm_vision_is_vision_describer(self) -> None:
        describer = LLMVisionDescriber()
        assert isinstance(describer, VisionDescriber)

    def test_litellm_transcriber_is_transcriber(self) -> None:
        t = LiteLLMTranscriber()
        assert isinstance(t, Transcriber)

    def test_faster_whisper_transcriber_skipped_if_not_installed(self) -> None:
        """FasterWhisperTranscriber raises ImportError if faster-whisper is missing."""
        with patch.dict("sys.modules", {"faster_whisper": None}):
            with pytest.raises(ImportError, match="faster-whisper"):
                FasterWhisperTranscriber()


# ---------------------------------------------------------------------------
# StubVisionDescriber
# ---------------------------------------------------------------------------


class TestStubVisionDescriber:
    @pytest.mark.asyncio
    async def test_returns_placeholder_with_filename(self) -> None:
        describer = StubVisionDescriber()
        result = await describer.describe("/path/to/photo.png")
        assert result == "[Image: photo.png]"

    @pytest.mark.asyncio
    async def test_ignores_prompt(self) -> None:
        describer = StubVisionDescriber()
        result = await describer.describe("/img.jpg", prompt="What is this?")
        assert result == "[Image: img.jpg]"


# ---------------------------------------------------------------------------
# LLMVisionDescriber
# ---------------------------------------------------------------------------


class TestLLMVisionDescriber:
    @pytest.mark.asyncio
    async def test_describe_calls_litellm(self, tmp_path) -> None:
        """Verify LLMVisionDescriber sends correct payload to litellm."""
        img_path = tmp_path / "test.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="A test image"))
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response
            describer = LLMVisionDescriber(model="gpt-4o-mini")
            result = await describer.describe(str(img_path))

        assert result == "A test image"
        mock_acompletion.assert_called_once()
        call_kwargs = mock_acompletion.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_describe_with_custom_prompt(self, tmp_path) -> None:
        img_path = tmp_path / "photo.jpg"
        img_path.write_bytes(b"\xff\xd8\xff")

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="A cat"))
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response
            describer = LLMVisionDescriber()
            result = await describer.describe(str(img_path), prompt="What animal?")

        assert result == "A cat"
        call_kwargs = mock_acompletion.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        content = messages[0]["content"]
        assert content[0]["text"] == "What animal?"
        assert "image/jpeg" in content[1]["image_url"]["url"]


# ---------------------------------------------------------------------------
# LiteLLMTranscriber
# ---------------------------------------------------------------------------


class TestLiteLLMTranscriber:
    @pytest.mark.asyncio
    async def test_transcribe_calls_litellm(self, tmp_path) -> None:
        audio_path = tmp_path / "speech.mp3"
        audio_path.write_bytes(b"fake audio data")

        mock_response = MagicMock()
        mock_response.text = "Hello world"

        with patch(
            "litellm.atranscription", new_callable=AsyncMock
        ) as mock_atranscription:
            mock_atranscription.return_value = mock_response
            transcriber = LiteLLMTranscriber(model="whisper-1")
            result = await transcriber.transcribe(str(audio_path))

        assert result == "Hello world"
        mock_atranscription.assert_called_once()

    @pytest.mark.asyncio
    async def test_transcribe_passes_language(self, tmp_path) -> None:
        audio_path = tmp_path / "speech.mp3"
        audio_path.write_bytes(b"fake audio data")

        mock_response = MagicMock()
        mock_response.text = "Hallo Welt"

        with patch(
            "litellm.atranscription", new_callable=AsyncMock
        ) as mock_atranscription:
            mock_atranscription.return_value = mock_response
            transcriber = LiteLLMTranscriber()
            result = await transcriber.transcribe(str(audio_path), language="de")

        assert result == "Hallo Welt"
        call_kwargs = mock_atranscription.call_args
        assert call_kwargs.kwargs.get("language") == "de"


# ---------------------------------------------------------------------------
# FasterWhisperTranscriber
# ---------------------------------------------------------------------------


class TestFasterWhisperTranscriber:
    @pytest.mark.asyncio
    async def test_transcribe_with_mock(self) -> None:
        """Verify transcription flow with mocked faster-whisper model."""
        mock_seg1 = MagicMock()
        mock_seg1.text = " Hello "
        mock_seg2 = MagicMock()
        mock_seg2.text = " world "

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_seg1, mock_seg2], MagicMock())

        mock_whisper_module = MagicMock()
        mock_whisper_module.WhisperModel.return_value = mock_model

        with patch.dict("sys.modules", {"faster_whisper": mock_whisper_module}):
            transcriber = FasterWhisperTranscriber.__new__(FasterWhisperTranscriber)
            transcriber._model = mock_model

            result = await transcriber.transcribe("/path/to/audio.wav")

        assert result == "Hello world"


# ---------------------------------------------------------------------------
# Image MIME detection
# ---------------------------------------------------------------------------


class TestImageMimeDetection:
    def test_png(self) -> None:
        assert _detect_image_mime("photo.png") == "image/png"

    def test_jpeg(self) -> None:
        assert _detect_image_mime("photo.jpg") == "image/jpeg"
        assert _detect_image_mime("photo.jpeg") == "image/jpeg"

    def test_gif(self) -> None:
        assert _detect_image_mime("anim.gif") == "image/gif"

    def test_webp(self) -> None:
        assert _detect_image_mime("photo.webp") == "image/webp"

    def test_unknown_defaults_to_png(self) -> None:
        assert _detect_image_mime("file.xyz") == "image/png"


# ---------------------------------------------------------------------------
# ProviderRegistry — multimodal
# ---------------------------------------------------------------------------


class TestProviderRegistryTranscriber:
    def test_disabled_returns_none(self) -> None:
        config = IntelligenceConfig(transcription_provider="disabled")
        result = ProviderRegistry.get_transcriber(config)
        assert result is None

    def test_api_returns_litellm_transcriber(self) -> None:
        config = IntelligenceConfig(transcription_provider="api")
        result = ProviderRegistry.get_transcriber(config)
        assert isinstance(result, LiteLLMTranscriber)

    def test_auto_without_faster_whisper_tries_litellm(self) -> None:
        """Auto mode falls back to LiteLLM when faster-whisper is missing."""
        config = IntelligenceConfig(transcription_provider="auto")
        with patch(
            "sagewai.intelligence.registry._try_faster_whisper",
            side_effect=ImportError("no faster-whisper"),
        ):
            result = ProviderRegistry.get_transcriber(config)
        # litellm is available in test env
        assert result is not None
        assert isinstance(result, LiteLLMTranscriber)

    def test_auto_returns_none_when_nothing_available(self) -> None:
        config = IntelligenceConfig(transcription_provider="auto")
        with patch(
            "sagewai.intelligence.registry._try_faster_whisper",
            side_effect=ImportError("no faster-whisper"),
        ), patch.dict("sys.modules", {"litellm": None}):
            result = ProviderRegistry.get_transcriber(config)
        assert result is None


class TestProviderRegistryVisionDescriber:
    def test_disabled_returns_stub(self) -> None:
        config = IntelligenceConfig(vision_provider="disabled")
        result = ProviderRegistry.get_vision_describer(config)
        assert isinstance(result, StubVisionDescriber)

    def test_api_returns_llm_vision(self) -> None:
        config = IntelligenceConfig(vision_provider="api")
        result = ProviderRegistry.get_vision_describer(config)
        assert isinstance(result, LLMVisionDescriber)

    def test_auto_with_litellm_returns_llm_vision(self) -> None:
        config = IntelligenceConfig(vision_provider="auto")
        result = ProviderRegistry.get_vision_describer(config)
        assert isinstance(result, LLMVisionDescriber)

    def test_auto_without_litellm_returns_stub(self) -> None:
        config = IntelligenceConfig(vision_provider="auto")
        with patch.dict("sys.modules", {"litellm": None}):
            result = ProviderRegistry.get_vision_describer(config)
        assert isinstance(result, StubVisionDescriber)


# ---------------------------------------------------------------------------
# IntelligenceConfig — multimodal fields
# ---------------------------------------------------------------------------


class TestIntelligenceConfigMultimodal:
    def test_defaults(self) -> None:
        config = IntelligenceConfig()
        assert config.transcription_provider == "auto"
        assert config.transcription_model == "base"
        assert config.vision_provider == "auto"
        assert config.vision_model == "gpt-4o-mini"

    def test_custom_values(self) -> None:
        config = IntelligenceConfig(
            transcription_provider="local",
            transcription_model="large-v3",
            vision_provider="disabled",
            vision_model="claude-3-haiku-20240307",
        )
        assert config.transcription_provider == "local"
        assert config.transcription_model == "large-v3"
        assert config.vision_provider == "disabled"
        assert config.vision_model == "claude-3-haiku-20240307"


# ---------------------------------------------------------------------------
# Parsers — MIME skip list
# ---------------------------------------------------------------------------


class TestMimeSkipList:
    def test_image_no_longer_skipped(self) -> None:
        from sagewai.context.parsers import _is_binary_mime

        assert not _is_binary_mime("image/png")
        assert not _is_binary_mime("image/jpeg")

    def test_audio_no_longer_skipped(self) -> None:
        from sagewai.context.parsers import _is_binary_mime

        assert not _is_binary_mime("audio/mpeg")
        assert not _is_binary_mime("audio/wav")

    def test_video_still_skipped(self) -> None:
        from sagewai.context.parsers import _is_binary_mime

        assert _is_binary_mime("video/mp4")

    def test_font_still_skipped(self) -> None:
        from sagewai.context.parsers import _is_binary_mime

        assert _is_binary_mime("font/woff2")
