# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Language detection with optional lingua-language-detector backend.

Provides ISO 639-1 language codes for text, falling back to ``"en"`` when
the optional ``lingua-language-detector`` package is not installed or when
detection confidence is too low.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class LanguageDetector:
    """Detect the language of text.

    Uses ``lingua-language-detector`` when available, otherwise always
    returns ``"en"`` as a safe default.

    Example::

        detector = LanguageDetector()
        detector.detect("Bonjour le monde")  # "fr"
        detector.detect("Hello world")       # "en"
    """

    def __init__(self) -> None:
        try:
            from lingua import Language, LanguageDetectorBuilder

            self._detector = LanguageDetectorBuilder.from_all_languages().build()
            self._available = True
            logger.debug("lingua-language-detector loaded successfully")
        except ImportError:
            self._detector = None
            self._available = False
            logger.debug(
                "lingua-language-detector not installed; "
                "language detection will default to 'en'"
            )

    @property
    def available(self) -> bool:
        """Whether the lingua backend is loaded."""
        return self._available

    def detect(self, text: str) -> str:
        """Detect the primary language of *text*.

        Returns:
            ISO 639-1 code (e.g. ``"en"``, ``"de"``, ``"zh"``, ``"ja"``).
            Falls back to ``"en"`` when detection is unavailable or uncertain.
        """
        if not self._available or not text.strip():
            return "en"

        result = self._detector.detect_language_of(text)  # type: ignore[union-attr]
        if result is None:
            return "en"
        return result.iso_code_639_1.name.lower()

    def detect_multiple(self, text: str) -> list[tuple[str, float]]:
        """Return ranked ``(language_code, confidence)`` pairs.

        Returns:
            List sorted by descending confidence. Empty list if detection
            is unavailable.
        """
        if not self._available or not text.strip():
            return [("en", 1.0)]

        results = self._detector.compute_language_confidence_values(text)  # type: ignore[union-attr]
        return [
            (r.language.iso_code_639_1.name.lower(), r.value) for r in results if r.value > 0.01
        ]
