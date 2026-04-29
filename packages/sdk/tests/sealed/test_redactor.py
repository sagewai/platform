"""Unit tests for Redactor / RedactionConfig / redact_text."""
from __future__ import annotations

from sagewai.sealed.redaction import (
    RedactionConfig,
    Redactor,
    redact_text,
)


def test_redactor_single_key_exact_match() -> None:
    redactor = Redactor({"OPENAI_API_KEY": "sk-very-long-value-32chars-aaaaa"})
    redacted, matched = redactor.redact(
        "calling api with sk-very-long-value-32chars-aaaaa now"
    )
    assert redacted == "calling api with <redacted:OPENAI_API_KEY> now"
    assert matched == ["OPENAI_API_KEY"]


def test_redactor_no_match_returns_input_unchanged() -> None:
    redactor = Redactor({"OPENAI_API_KEY": "sk-not-present-in-text-12345678"})
    redacted, matched = redactor.redact("hello world")
    assert redacted == "hello world"
    assert matched == []


def test_redactor_multiple_keys_no_overlap() -> None:
    redactor = Redactor({
        "ANTHROPIC_API_KEY": "sk-ant-aaaaaaaaaaaaaaaaaaaaaaaa",
        "OPENAI_API_KEY":    "sk-openai-bbbbbbbbbbbbbbbbbbb",
    })
    text = "anthropic: sk-ant-aaaaaaaaaaaaaaaaaaaaaaaa, openai: sk-openai-bbbbbbbbbbbbbbbbbbb"
    redacted, matched = redactor.redact(text)
    assert "<redacted:ANTHROPIC_API_KEY>" in redacted
    assert "<redacted:OPENAI_API_KEY>" in redacted
    assert "sk-ant-aaaaaaaaaaaaaaaaaaaaaaaa" not in redacted
    assert "sk-openai-bbbbbbbbbbbbbbbbbbb" not in redacted
    assert sorted(matched) == ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]


def test_redactor_overlapping_prefix_longest_first_wins() -> None:
    # Both values present; the longer is a superset of the shorter prefix.
    redactor = Redactor({
        "SHORT": "sk-aaaaaaaa",
        "LONG":  "sk-aaaaaaaaXXXXXXXX",
    })
    redacted, matched = redactor.redact("see sk-aaaaaaaaXXXXXXXX here")
    assert redacted == "see <redacted:LONG> here"
    assert matched == ["LONG"]


def test_redactor_does_not_redact_key_names() -> None:
    redactor = Redactor({"OPENAI_API_KEY": "sk-very-long-value-32chars-aaaaa"})
    redacted, matched = redactor.redact(
        "the env var is OPENAI_API_KEY and value is sk-very-long-value-32chars-aaaaa"
    )
    # Key NAME is NOT redacted; only the value is
    assert "OPENAI_API_KEY" in redacted
    assert "<redacted:OPENAI_API_KEY>" in redacted
    assert "sk-very-long-value-32chars-aaaaa" not in redacted
    assert matched == ["OPENAI_API_KEY"]


def test_redactor_empty_value_skipped() -> None:
    redactor = Redactor({"EMPTY": "", "REAL": "sk-real-aaaaaaaaaaaaaa"})
    redacted, matched = redactor.redact("hello sk-real-aaaaaaaaaaaaaa world ")
    # Empty value is skipped; "" would otherwise match every position
    assert "<redacted:EMPTY>" not in redacted
    assert "<redacted:REAL>" in redacted
    assert matched == ["REAL"]


def test_redactor_below_min_length_skipped() -> None:
    config = RedactionConfig(min_value_length=8)
    redactor = Redactor({"DEBUG_LEVEL": "1", "REAL": "sk-real-aaaaaaaaaaaaaa"}, config=config)
    redacted, matched = redactor.redact("DEBUG=1 secret=sk-real-aaaaaaaaaaaaaa")
    # "1" is below min_value_length, won't be redacted
    assert "DEBUG=1" in redacted
    assert "<redacted:REAL>" in redacted
    assert matched == ["REAL"]


def test_redactor_idempotent() -> None:
    redactor = Redactor({"KEY": "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaa"})
    once, _ = redactor.redact("see sk-aaaaaaaaaaaaaaaaaaaaaaaaaaa now")
    twice, second_pass_matched = redactor.redact(once)
    assert once == twice
    assert second_pass_matched == []


def test_redactor_oversize_input_bypassed_with_audit_marker() -> None:
    config = RedactionConfig(max_input_bytes=100)
    redactor = Redactor({"KEY": "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaa"}, config=config)
    big = "a" * 200 + "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaa"
    redacted, matched = redactor.redact(big)
    # Oversize input bypasses redaction (loud failure). Caller is responsible
    # for emitting redaction.skipped_oversize via redact_and_audit.
    assert redacted == big
    assert matched == []
    assert redactor.last_skipped_oversize is True


def test_redactor_value_count_property() -> None:
    redactor = Redactor({
        "EMPTY": "",
        "SHORT": "abc",
        "OK":    "sk-aaaaaaaaaaaaaaa",
    })
    # EMPTY skipped (empty); SHORT skipped (below default min_value_length=8); OK active
    assert redactor.value_count == 1


def test_redact_text_free_function_parity() -> None:
    secrets = {"KEY": "sk-aaaaaaaaaaaaaaaaaaa"}
    via_class, _ = Redactor(secrets).redact("see sk-aaaaaaaaaaaaaaaaaaa")
    via_func, _ = redact_text("see sk-aaaaaaaaaaaaaaaaaaa", secret_values=secrets)
    assert via_class == via_func


def test_redact_dict_recursive_string_leaves_only() -> None:
    redactor = Redactor({"KEY": "sk-aaaaaaaaaaaaaaaaaaa"})
    payload = {
        "outer": "see sk-aaaaaaaaaaaaaaaaaaa",
        "nested": {"inner": "and sk-aaaaaaaaaaaaaaaaaaa here"},
        "list": ["one sk-aaaaaaaaaaaaaaaaaaa", "two", 42, None, True],
        "non_string": 12345,
    }
    out, matched = redactor.redact_dict(payload)
    assert out["outer"] == "see <redacted:KEY>"
    assert out["nested"]["inner"] == "and <redacted:KEY> here"
    assert out["list"][0] == "one <redacted:KEY>"
    assert out["list"][1] == "two"
    assert out["list"][2] == 42
    assert out["list"][3] is None
    assert out["list"][4] is True
    assert out["non_string"] == 12345
    assert matched == ["KEY"]
