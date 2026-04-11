# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Multi-language support: detection and universal segmentation."""

from sagewai.intelligence.language.detector import LanguageDetector
from sagewai.intelligence.language.segmenter import UniversalSegmenter

__all__ = ["LanguageDetector", "UniversalSegmenter"]
