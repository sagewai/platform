"""Tests for the file-backed blueprint cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from sagewai.autopilot.sagewai_llm.cache import BlueprintCache


def _sample_blueprint_json() -> str:
    return '{"id":"SYNTHETIC_test","version":"0.0.1"}'


def test_cache_miss_returns_none(tmp_path: Path):
    c = BlueprintCache(tmp_path, ttl_seconds=3600)
    assert c.get("key-1") is None


def test_cache_hit_within_ttl(tmp_path: Path):
    c = BlueprintCache(tmp_path, ttl_seconds=3600)
    c.put("key-1", _sample_blueprint_json())
    got = c.get("key-1")
    assert got == _sample_blueprint_json()


def test_cache_expiry_after_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    times = [1000.0]

    def fake_time() -> float:
        return times[0]

    c = BlueprintCache(tmp_path, ttl_seconds=60, clock=fake_time)
    c.put("key-1", _sample_blueprint_json())
    assert c.get("key-1") is not None
    times[0] = 1061.0  # 61 seconds later, past the 60-second TTL
    assert c.get("key-1") is None


def test_cache_delete_removes_entry(tmp_path: Path):
    c = BlueprintCache(tmp_path, ttl_seconds=3600)
    c.put("key-1", _sample_blueprint_json())
    c.delete("key-1")
    assert c.get("key-1") is None


def test_cache_delete_on_missing_key_is_noop(tmp_path: Path):
    c = BlueprintCache(tmp_path, ttl_seconds=3600)
    c.delete("absent")  # should not raise


def test_cache_clear_removes_all_entries(tmp_path: Path):
    c = BlueprintCache(tmp_path, ttl_seconds=3600)
    c.put("k1", "v1")
    c.put("k2", "v2")
    c.clear()
    assert c.get("k1") is None
    assert c.get("k2") is None


def test_cache_persists_across_instances(tmp_path: Path):
    BlueprintCache(tmp_path, ttl_seconds=3600).put("k", _sample_blueprint_json())
    fresh = BlueprintCache(tmp_path, ttl_seconds=3600)
    assert fresh.get("k") == _sample_blueprint_json()


def test_cache_refuses_to_escape_dir_via_path_traversal(tmp_path: Path):
    c = BlueprintCache(tmp_path, ttl_seconds=3600)
    with pytest.raises(ValueError):
        c.put("../escape", "x")
    with pytest.raises(ValueError):
        c.put("/absolute", "x")


def test_cache_creates_dir_if_missing(tmp_path: Path):
    subdir = tmp_path / "nested" / "cache"
    c = BlueprintCache(subdir, ttl_seconds=3600)
    c.put("k", "v")
    assert subdir.exists()
