# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Universal sentence segmentation and tokenization across major language scripts.

Handles Latin (EN/DE/FR/ES/TR/PL/RU), CJK (ZH/JA/KO), and Arabic (AR) text
for sentence splitting and keyword tokenization. Falls back to simple regex
patterns when no language detector is available.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sagewai.intelligence.language.detector import LanguageDetector

# ---------------------------------------------------------------------------
# Script-specific sentence-boundary patterns
# ---------------------------------------------------------------------------

# Latin/Cyrillic: split after .!? followed by whitespace (no uppercase
# requirement -- handles DE noun-initial sentences, TR, PL, RU, etc.)
_LATIN_END = re.compile(r"(?<=[.!?])\s+")

# CJK full-width terminators + half-width for mixed-script text
_CJK_END = re.compile(r"(?<=[。！？.!?])\s*")

# Arabic question mark ؟ plus Latin .!
_ARABIC_END = re.compile(r"(?<=[.؟!])\s+")

# Korean Hangul syllable range
_HANGUL_OR_WORD = re.compile(r"[\uac00-\ud7af]+|\w+")

# Mapping of language codes to script families
_CJK_LANGS = {"zh", "ja", "ko"}
_ARABIC_LANGS = {"ar"}


class UniversalSegmenter:
    """Split text into sentences and tokens across major language scripts.

    When a :class:`LanguageDetector` is provided, the script family is
    auto-detected. Otherwise, callers can pass an explicit ``language``
    code or accept the default Latin-script behaviour.

    Example::

        segmenter = UniversalSegmenter()
        segmenter.split_sentences("Hello world. How are you?")
        # ["Hello world.", "How are you?"]  -- wait, the period stays?
        # Actually the split removes the boundary: ["Hello world.", "How are you?"]
    """

    def __init__(self, detector: LanguageDetector | None = None) -> None:
        self._detector = detector

    # ------------------------------------------------------------------
    # Script heuristic (fallback when detector unavailable or uncertain)
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_script(text: str) -> str | None:
        """Heuristic script detection based on character ranges.

        Returns a language code hint (``"zh"``, ``"ko"``, ``"ar"``) or
        ``None`` if the text looks Latin/Cyrillic (the default path).
        """
        cjk_count = 0
        hangul_count = 0
        arabic_count = 0
        total = 0
        for ch in text:
            if ch.isspace():
                continue
            total += 1
            cp = ord(ch)
            if (
                0x4E00 <= cp <= 0x9FFF  # CJK Unified
                or 0x3400 <= cp <= 0x4DBF
                or 0xF900 <= cp <= 0xFAFF
                or 0x3040 <= cp <= 0x309F  # Hiragana
                or 0x30A0 <= cp <= 0x30FF  # Katakana
            ):
                cjk_count += 1
            elif 0xAC00 <= cp <= 0xD7AF:  # Hangul
                hangul_count += 1
            elif 0x0600 <= cp <= 0x06FF:  # Arabic
                arabic_count += 1

        if total == 0:
            return None

        ratio_threshold = 0.3
        if cjk_count / total > ratio_threshold:
            return "zh"  # covers ja too (same tokenization)
        if hangul_count / total > ratio_threshold:
            return "ko"
        if arabic_count / total > ratio_threshold:
            return "ar"
        return None

    # ------------------------------------------------------------------
    # Sentence splitting
    # ------------------------------------------------------------------

    def split_sentences(
        self,
        text: str,
        language: str | None = None,
    ) -> list[str]:
        """Split *text* into sentences.

        Args:
            text: Input text (any script).
            language: Optional ISO 639-1 code. Auto-detected when omitted
                and a detector is available.

        Returns:
            List of non-empty, stripped sentence strings.
        """
        if not language:
            if self._detector and self._detector.available:
                language = self._detector.detect(text)
            else:
                language = self._detect_script(text) or "en"

        language = language.lower()

        if language in _CJK_LANGS:
            return self._split_cjk(text)
        elif language in _ARABIC_LANGS:
            return self._split_arabic(text)
        else:
            return self._split_latin(text)

    def _split_latin(self, text: str) -> list[str]:
        """Latin/Cyrillic script: split on ``.!?`` followed by whitespace."""
        sentences = _LATIN_END.split(text.strip())
        return [s.strip() for s in sentences if s.strip()]

    def _split_cjk(self, text: str) -> list[str]:
        """CJK: split on full-width ``。！？`` and half-width ``.!?``."""
        sentences = _CJK_END.split(text.strip())
        return [s.strip() for s in sentences if s.strip()]

    def _split_arabic(self, text: str) -> list[str]:
        """Arabic: split on ``؟`` (Arabic question mark), ``.`` and ``!``."""
        sentences = _ARABIC_END.split(text.strip())
        return [s.strip() for s in sentences if s.strip()]

    # ------------------------------------------------------------------
    # Tokenization (for BM25 / keyword matching)
    # ------------------------------------------------------------------

    def tokenize(
        self,
        text: str,
        language: str | None = None,
    ) -> list[str]:
        """Tokenize *text* for BM25 or keyword matching.

        Strategy by script family:
        - **Chinese/Japanese**: character-level bigrams (standard IR approach).
        - **Korean**: syllable-level tokens (Hangul syllables are atomic).
        - **Latin/Cyrillic/Arabic**: word-level via ``\\w+``.

        Args:
            text: Input text.
            language: Optional ISO 639-1 code.

        Returns:
            List of lowercased tokens (minimum length 2 for word-level).
        """
        if not language:
            if self._detector and self._detector.available:
                language = self._detector.detect(text)
            else:
                language = self._detect_script(text) or "en"

        language = language.lower()
        text_lower = text.lower()

        if language in ("zh", "ja"):
            return self._tokenize_cjk_bigrams(text_lower)
        elif language == "ko":
            return self._tokenize_korean(text_lower)
        else:
            return self._tokenize_words(text_lower)

    def _tokenize_cjk_bigrams(self, text: str) -> list[str]:
        """Character bigrams for Chinese/Japanese."""
        chars = [c for c in text if not c.isspace()]
        if len(chars) < 2:
            return chars
        return [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]

    def _tokenize_korean(self, text: str) -> list[str]:
        """Syllable-level tokens for Korean Hangul."""
        return [t for t in _HANGUL_OR_WORD.findall(text) if len(t) >= 2]

    def _tokenize_words(self, text: str) -> list[str]:
        """Word-level tokens for Latin/Cyrillic/Arabic scripts."""
        return [t for t in re.findall(r"\w+", text) if len(t) >= 2]
