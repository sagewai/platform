# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.text."""
import pytest

from sagewai.tools.builtins import text as text_mod


# ── diff_text ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_diff_text_unified_default():
    out = await text_mod.diff_text({"a": "hello\n", "b": "world\n"})
    assert "hello" in out["diff"]
    assert "world" in out["diff"]
    assert out["equal"] is False


@pytest.mark.asyncio
async def test_diff_text_equal_strings():
    out = await text_mod.diff_text({"a": "same\n", "b": "same\n"})
    assert out["equal"] is True
    assert out["diff"] == ""


@pytest.mark.asyncio
async def test_diff_text_context_mode():
    out = await text_mod.diff_text({"a": "one\ntwo\n", "b": "one\nTWO\n", "mode": "context"})
    assert "***" in out["diff"] or "---" in out["diff"]
    assert out["equal"] is False


@pytest.mark.asyncio
async def test_diff_text_ndiff_mode():
    out = await text_mod.diff_text({"a": "abc\n", "b": "abd\n", "mode": "ndiff"})
    assert any(line.startswith(("-", "+", "?")) for line in out["diff"].splitlines())


@pytest.mark.asyncio
async def test_diff_text_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown mode"):
        await text_mod.diff_text({"a": "x", "b": "y", "mode": "fancy"})


# ── structured_write ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_structured_write_top_level_substitution():
    out = await text_mod.structured_write({
        "template": {"name": "{{ name }}", "city": "{{ city }}"},
        "values": {"name": "Ada", "city": "Berlin"},
    })
    assert out == {"output": {"name": "Ada", "city": "Berlin"}}


@pytest.mark.asyncio
async def test_structured_write_nested_dicts_and_lists():
    out = await text_mod.structured_write({
        "template": {"a": {"b": ["{{ x }}", "static"]}},
        "values": {"x": "filled"},
    })
    assert out == {"output": {"a": {"b": ["filled", "static"]}}}


@pytest.mark.asyncio
async def test_structured_write_missing_key_leaves_placeholder():
    out = await text_mod.structured_write({
        "template": {"k": "{{ missing }}"},
        "values": {},
    })
    assert out["output"] == {"k": "{{ missing }}"}


@pytest.mark.asyncio
async def test_structured_write_non_string_leaves_unchanged():
    out = await text_mod.structured_write({
        "template": {"n": 42, "b": True, "z": None},
        "values": {"n": "won't apply"},
    })
    assert out["output"] == {"n": 42, "b": True, "z": None}
