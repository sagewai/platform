# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Multimodal content support and processing.

Phase I5: ContentPart model for image, audio, and video in messages.
Phase I6: Transcriber and VisionDescriber protocols with local + API backends.
"""

from sagewai.intelligence.multimodal.message import ContentPart, ContentType
from sagewai.intelligence.multimodal.protocol import Transcriber, VisionDescriber
from sagewai.intelligence.multimodal.vision import LLMVisionDescriber, StubVisionDescriber
from sagewai.intelligence.multimodal.whisper import (
    FasterWhisperTranscriber,
    LiteLLMTranscriber,
)

__all__ = [
    "ContentPart",
    "ContentType",
    "FasterWhisperTranscriber",
    "LiteLLMTranscriber",
    "LLMVisionDescriber",
    "StubVisionDescriber",
    "Transcriber",
    "VisionDescriber",
]
