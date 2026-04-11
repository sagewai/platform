"""Tests for sagewai.fleet.normalizer — ModelNormalizer."""

from __future__ import annotations

import pytest

from sagewai.fleet.normalizer import ModelNormalizer


class TestNormalize:
    """ModelNormalizer.normalize() tests."""

    def test_strips_openai_prefix(self) -> None:
        assert ModelNormalizer.normalize("openai/gpt-4o") == "gpt-4o"

    def test_strips_anthropic_prefix(self) -> None:
        assert ModelNormalizer.normalize("anthropic/claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_strips_ollama_prefix_and_replaces_colon(self) -> None:
        assert ModelNormalizer.normalize("ollama/llama3:70b") == "llama3-70b"

    def test_strips_google_prefix(self) -> None:
        assert ModelNormalizer.normalize("google/gemini-1.5-pro") == "gemini-1.5-pro"

    def test_lowercases(self) -> None:
        assert ModelNormalizer.normalize("GPT-4o") == "gpt-4o"

    def test_strips_whitespace(self) -> None:
        assert ModelNormalizer.normalize("  gpt-4o  ") == "gpt-4o"

    def test_empty_string(self) -> None:
        assert ModelNormalizer.normalize("") == ""

    def test_no_prefix(self) -> None:
        assert ModelNormalizer.normalize("gpt-4o") == "gpt-4o"

    def test_multiple_slashes_strips_first_only(self) -> None:
        # Only the first slash is treated as a provider separator
        assert ModelNormalizer.normalize("azure/openai/gpt-4o") == "openai-gpt-4o"

    def test_colon_in_tag(self) -> None:
        assert ModelNormalizer.normalize("llama3:70b-q4") == "llama3-70b-q4"

    def test_multiple_colons(self) -> None:
        assert ModelNormalizer.normalize("model:tag:variant") == "model-tag-variant"

    def test_underscores_collapsed(self) -> None:
        assert ModelNormalizer.normalize("my__model") == "my-model"

    def test_mixed_separators(self) -> None:
        assert ModelNormalizer.normalize("my-_-model") == "my-model"

    def test_leading_trailing_hyphens_stripped(self) -> None:
        assert ModelNormalizer.normalize("-gpt-4o-") == "gpt-4o"

    def test_preserves_dots(self) -> None:
        assert ModelNormalizer.normalize("gemini-1.5-pro") == "gemini-1.5-pro"

    def test_vertex_ai_prefix(self) -> None:
        assert ModelNormalizer.normalize("vertex_ai/gemini-pro") == "gemini-pro"

    def test_together_prefix(self) -> None:
        assert (
            ModelNormalizer.normalize("together/meta-llama/Llama-3-70b")
            == "meta-llama-llama-3-70b"
        )


class TestCanonicalList:
    """ModelNormalizer.canonical_list() tests."""

    def test_deduplicates(self) -> None:
        result = ModelNormalizer.canonical_list(
            ["openai/gpt-4o", "gpt-4o", "GPT-4o"]
        )
        assert result == ["gpt-4o"]

    def test_preserves_order(self) -> None:
        result = ModelNormalizer.canonical_list(
            ["openai/gpt-4o", "ollama/llama3:70b", "anthropic/claude-sonnet-4-6"]
        )
        assert result == ["gpt-4o", "llama3-70b", "claude-sonnet-4-6"]

    def test_skips_empty_strings(self) -> None:
        result = ModelNormalizer.canonical_list(["gpt-4o", "", "  ", "llama3"])
        assert result == ["gpt-4o", "llama3"]

    def test_empty_list(self) -> None:
        assert ModelNormalizer.canonical_list([]) == []

    def test_all_same_model(self) -> None:
        result = ModelNormalizer.canonical_list(
            ["openai/gpt-4o", "gpt-4o", "  GPT-4o  "]
        )
        assert result == ["gpt-4o"]
