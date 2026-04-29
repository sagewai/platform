"""AuditWriter default_redactor integration — defense-in-depth scrubbing
of details/context payloads at write time."""
from __future__ import annotations

import json
from typing import Any

import pytest

from sagewai.sealed.audit import AuditWriter
from sagewai.sealed.redaction import Redactor


class FakePool:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> None:
        self.executed.append((sql, args))


class FakeStore:
    def __init__(self) -> None:
        self._pool = FakePool()


@pytest.mark.asyncio
async def test_audit_writer_redacts_details_when_default_redactor_present() -> None:
    store = FakeStore()
    redactor = Redactor({"OPENAI_API_KEY": "sk-aaaaaaaaaaaaaaaaaaaaaaaaa"})
    writer = AuditWriter(store, default_redactor=redactor)

    await writer.emit(
        event_type="profile.injected",
        details={"sloppy": "value is sk-aaaaaaaaaaaaaaaaaaaaaaaaa here"},
    )

    sql, args = store._pool.executed[-1]
    # Last positional arg is the details JSON
    details_json = args[-1]
    parsed = json.loads(details_json)
    assert "sk-aaaaaaaaaaaaaaaaaaaaaaaaa" not in details_json
    assert parsed["sloppy"] == "value is <redacted:OPENAI_API_KEY> here"


@pytest.mark.asyncio
async def test_audit_writer_no_redactor_preserves_old_behavior() -> None:
    store = FakeStore()
    writer = AuditWriter(store)  # no redactor passed

    await writer.emit(
        event_type="profile.injected",
        details={"keys": ["X", "Y"]},
    )

    sql, args = store._pool.executed[-1]
    details_json = args[-1]
    parsed = json.loads(details_json)
    assert parsed == {"keys": ["X", "Y"]}


@pytest.mark.asyncio
async def test_audit_writer_redacts_context_too() -> None:
    store = FakeStore()
    redactor = Redactor({"K": "sk-aaaaaaaaaaaaaaaaa"})
    writer = AuditWriter(store, default_redactor=redactor)

    await writer.emit(
        event_type="profile.injected",
        context={"who": "see sk-aaaaaaaaaaaaaaaaa actor"},
        details={"clean": "no value"},
    )

    sql, args = store._pool.executed[-1]
    details_json = args[-1]
    parsed = json.loads(details_json)
    assert "sk-aaaaaaaaaaaaaaaaa" not in details_json
    assert parsed["who"] == "see <redacted:K> actor"
    assert parsed["clean"] == "no value"
