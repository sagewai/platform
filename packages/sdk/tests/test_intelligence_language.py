# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the intelligence language module: detection and segmentation."""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# LanguageDetector
# ---------------------------------------------------------------------------


class TestLanguageDetector:
    """Tests for LanguageDetector."""

    def test_fallback_returns_en_without_lingua(self) -> None:
        """When lingua is not installed, detect() always returns 'en'."""
        with patch.dict("sys.modules", {"lingua": None}):
            # Force re-import to trigger ImportError path
            from sagewai.intelligence.language.detector import LanguageDetector

            detector = LanguageDetector()
            assert detector.available is False
            assert detector.detect("Hallo Welt") == "en"

    def test_detect_empty_string_returns_en(self) -> None:
        from sagewai.intelligence.language.detector import LanguageDetector

        detector = LanguageDetector()
        assert detector.detect("") == "en"
        assert detector.detect("   ") == "en"

    def test_detect_multiple_fallback(self) -> None:
        """detect_multiple returns [('en', 1.0)] when unavailable."""
        with patch.dict("sys.modules", {"lingua": None}):
            from sagewai.intelligence.language.detector import LanguageDetector

            detector = LanguageDetector()
            result = detector.detect_multiple("test text")
            assert result == [("en", 1.0)]


class TestLanguageDetectorWithLingua:
    """Tests that require lingua-language-detector to be installed."""

    @pytest.fixture(autouse=True)
    def _skip_without_lingua(self) -> None:
        try:
            import lingua  # noqa: F401
        except ImportError:
            pytest.skip("lingua-language-detector not installed")

    def test_detect_english(self) -> None:
        from sagewai.intelligence.language.detector import LanguageDetector

        detector = LanguageDetector()
        assert detector.available is True
        assert detector.detect(
            "The quick brown fox jumps over the lazy dog."
        ) == "en"

    def test_detect_german(self) -> None:
        from sagewai.intelligence.language.detector import LanguageDetector

        detector = LanguageDetector()
        result = detector.detect(
            "Die schnelle braune Fuchs springt ueber den faulen Hund."
        )
        assert result == "de"

    def test_detect_turkish(self) -> None:
        from sagewai.intelligence.language.detector import LanguageDetector

        detector = LanguageDetector()
        result = detector.detect(
            "Merhaba dunya, bugun hava cok guzel ve sicak."
        )
        assert result == "tr"

    def test_detect_chinese(self) -> None:
        from sagewai.intelligence.language.detector import LanguageDetector

        detector = LanguageDetector()
        result = detector.detect("今天天气很好，我们去公园散步吧。")
        assert result == "zh"

    def test_detect_japanese(self) -> None:
        from sagewai.intelligence.language.detector import LanguageDetector

        detector = LanguageDetector()
        result = detector.detect("今日はとても良い天気ですね。散歩に行きましょう。")
        assert result == "ja"

    def test_detect_korean(self) -> None:
        from sagewai.intelligence.language.detector import LanguageDetector

        detector = LanguageDetector()
        result = detector.detect("오늘 날씨가 정말 좋습니다. 산책하러 갈까요?")
        assert result == "ko"

    def test_detect_arabic(self) -> None:
        from sagewai.intelligence.language.detector import LanguageDetector

        detector = LanguageDetector()
        result = detector.detect("الطقس جميل اليوم، هل نذهب في نزهة؟")
        assert result == "ar"

    def test_detect_russian(self) -> None:
        from sagewai.intelligence.language.detector import LanguageDetector

        detector = LanguageDetector()
        result = detector.detect(
            "Сегодня прекрасная погода. Давайте пойдем гулять в парк."
        )
        assert result == "ru"

    def test_detect_multiple_returns_ranked(self) -> None:
        from sagewai.intelligence.language.detector import LanguageDetector

        detector = LanguageDetector()
        results = detector.detect_multiple(
            "The quick brown fox jumps over the lazy dog."
        )
        assert len(results) > 0
        # First result should be English
        assert results[0][0] == "en"
        # Confidence should be a float between 0 and 1
        assert 0 < results[0][1] <= 1.0


# ---------------------------------------------------------------------------
# UniversalSegmenter — sentence splitting
# ---------------------------------------------------------------------------


class TestUniversalSegmenterSentences:
    """Test sentence splitting across languages."""

    def test_latin_english(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        result = seg.split_sentences(
            "Hello world. How are you? I am fine!", language="en"
        )
        assert len(result) == 3
        assert result[0] == "Hello world."
        assert result[1] == "How are you?"
        assert result[2] == "I am fine!"

    def test_latin_german_no_uppercase_requirement(self) -> None:
        """German sentences starting with lowercase (after abbreviation) work."""
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        result = seg.split_sentences(
            "Das ist gut. das ist auch gut.", language="de"
        )
        assert len(result) == 2

    def test_latin_french(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        result = seg.split_sentences(
            "Bonjour le monde. Comment allez-vous?", language="fr"
        )
        assert len(result) == 2

    def test_cjk_chinese(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        result = seg.split_sentences(
            "今天天气很好。我们去公园散步吧！你觉得怎么样？",
            language="zh",
        )
        assert len(result) == 3

    def test_cjk_japanese(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        result = seg.split_sentences(
            "今日はとても良い天気ですね。散歩に行きましょう。",
            language="ja",
        )
        assert len(result) == 2

    def test_arabic_sentences(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        result = seg.split_sentences(
            "الطقس جميل اليوم. هل نذهب في نزهة؟",
            language="ar",
        )
        assert len(result) == 2

    def test_empty_text(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        assert seg.split_sentences("", language="en") == []
        assert seg.split_sentences("   ", language="en") == []

    def test_single_sentence_no_terminator(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        result = seg.split_sentences("Hello world", language="en")
        assert result == ["Hello world"]

    def test_autodetect_with_detector(self) -> None:
        """When detector is provided and no language given, auto-detect."""
        from unittest.mock import MagicMock

        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        mock_detector = MagicMock()
        mock_detector.detect.return_value = "zh"

        seg = UniversalSegmenter(detector=mock_detector)
        result = seg.split_sentences("今天天气很好。你好吗？")
        assert len(result) == 2
        mock_detector.detect.assert_called_once()


# ---------------------------------------------------------------------------
# UniversalSegmenter — tokenization
# ---------------------------------------------------------------------------


class TestUniversalSegmenterTokenize:
    """Test tokenization across languages."""

    def test_latin_word_tokens(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        tokens = seg.tokenize("Hello World! How are you?", language="en")
        assert "hello" in tokens
        assert "world" in tokens
        assert "how" in tokens
        # Single-char tokens excluded
        assert all(len(t) >= 2 for t in tokens)

    def test_cjk_bigrams_chinese(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        tokens = seg.tokenize("你好世界", language="zh")
        # Should produce character bigrams: 你好, 好世, 世界
        assert len(tokens) == 3
        assert tokens[0] == "你好"
        assert tokens[1] == "好世"
        assert tokens[2] == "世界"

    def test_cjk_bigrams_japanese(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        tokens = seg.tokenize("東京タワー", language="ja")
        # 5 chars → 4 bigrams: 東京, 京タ, タワ, ワー
        assert len(tokens) == 4

    def test_korean_syllable_tokens(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        tokens = seg.tokenize("오늘 날씨가 좋습니다", language="ko")
        # Should produce syllable-level tokens >= 2 chars
        assert len(tokens) > 0
        assert all(len(t) >= 2 for t in tokens)

    def test_single_cjk_char(self) -> None:
        """Single CJK character returns the character itself (no bigram possible)."""
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        tokens = seg.tokenize("你", language="zh")
        assert tokens == ["你"]

    def test_empty_text_tokenize(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        tokens = seg.tokenize("", language="en")
        assert tokens == []

    def test_cyrillic_word_tokens(self) -> None:
        from sagewai.intelligence.language.segmenter import UniversalSegmenter

        seg = UniversalSegmenter()
        tokens = seg.tokenize("Привет мир!", language="ru")
        assert "привет" in tokens
        assert "мир" in tokens


# ---------------------------------------------------------------------------
# Integration: BM25 with multilingual tokenization
# ---------------------------------------------------------------------------


class TestBM25Integration:
    """Test BM25 index works with non-Latin text via wired segmenter."""

    def test_bm25_chinese_search(self) -> None:
        from sagewai.context.bm25 import BM25Index

        idx = BM25Index()
        idx.add("doc1", "今天天气很好我们去公园散步")
        idx.add("doc2", "明天会下雨请带伞")
        results = idx.search("天气公园", top_k=5)
        # doc1 should score higher (shares more bigrams with query)
        assert len(results) > 0
        assert results[0][0] == "doc1"

    def test_bm25_latin_still_works(self) -> None:
        from sagewai.context.bm25 import BM25Index

        idx = BM25Index()
        idx.add("doc1", "The quick brown fox jumps over the lazy dog")
        idx.add("doc2", "A cat sleeps on the mat")
        results = idx.search("brown fox", top_k=5)
        assert len(results) > 0
        assert results[0][0] == "doc1"

    def test_bm25_german_search(self) -> None:
        from sagewai.context.bm25 import BM25Index

        idx = BM25Index()
        idx.add("doc1", "Die Katze schlaeft auf der Matte")
        idx.add("doc2", "Der Hund laeuft im Park")
        results = idx.search("Katze Matte", top_k=5)
        assert len(results) > 0
        assert results[0][0] == "doc1"


# ---------------------------------------------------------------------------
# Integration: Compressor with multilingual sentence splitting
# ---------------------------------------------------------------------------


class TestCompressorIntegration:
    """Test compressor works with non-Latin text."""

    def test_compress_cjk_text(self) -> None:
        from sagewai.directives.compressor import compress_text

        text = "今天天气很好。我们去公园散步吧。公园里有很多花。花很漂亮。"
        result = compress_text(text, query="公园散步", target_tokens=30)
        # Should produce a compressed result without crashing
        assert isinstance(result, str)
        assert len(result) > 0

    def test_compress_latin_still_works(self) -> None:
        from sagewai.directives.compressor import compress_text

        text = (
            "The weather is nice today. We should go to the park. "
            "The park has many flowers. The flowers are beautiful."
        )
        result = compress_text(text, query="park flowers", target_tokens=30)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_compress_german_text(self) -> None:
        from sagewai.directives.compressor import compress_text

        text = (
            "Das Wetter ist heute schoen. Wir sollten in den Park gehen. "
            "Der Park hat viele Blumen. Die Blumen sind wunderschoen."
        )
        result = compress_text(text, query="Park Blumen", target_tokens=30)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_compress_short_text_unchanged(self) -> None:
        """Text that already fits the budget should be returned unchanged."""
        from sagewai.directives.compressor import compress_text

        text = "短いテキスト。"
        result = compress_text(text, query="テキスト", target_tokens=100)
        assert result == text
