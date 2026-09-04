# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OperatorActivity is bounded, redacted, and stored per run with a hard row cap."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from sagewai.work import FLEET_ACTIVITY_LOG_MAX_BYTES, bounded_ndjson
from sagewai.work.activity import (
    ACTIVITY_LOG_MAX_BYTES,
    ACTIVITY_ROW_CAP,
    ListActivitySink,
    OperatorActivity,
    WorkActivityStore,
    activity_redactor,
)
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def _activity(sequence: int, **overrides) -> OperatorActivity:
    values = dict(
        project_id="p", work_id="w", run_id="w:implement:1", sequence=sequence, at=NOW,
        source="codex", kind="message", summary=f"line {sequence}",
    )
    values.update(overrides)
    return OperatorActivity(**values)


def test_activity_bounds_summary_and_detail() -> None:
    long = "x" * 3000
    item = _activity(1, summary=long, detail="y" * 9000)
    assert len(item.summary) == 2000
    assert len(item.detail) == 8192
    with pytest.raises(ValueError):
        _activity(1, kind="unknown")


def test_redactor_replaces_scoped_credential_values() -> None:
    redact = activity_redactor({"GITHUB_TOKEN": "ghp_secret123", "EMPTY": ""})
    item = redact(_activity(1, summary="token ghp_secret123 used", detail="ghp_secret123"))
    assert item.summary == "token [REDACTED:GITHUB_TOKEN] used"
    assert item.detail == "[REDACTED:GITHUB_TOKEN]"
    longest = activity_redactor({"SHORT": "abc", "LONG": "abcdef"})
    assert longest(_activity(1, summary="value abcdef here")).summary == "value [REDACTED:LONG] here"
    grown = activity_redactor({"TOKEN": "ab"})(_activity(1, summary="ab" * 1000, detail="ab" * 4096))
    assert len(grown.summary) == 2000
    assert len(grown.detail) == 8192


def test_list_sink_collects_in_order() -> None:
    sink = ListActivitySink()
    sink.emit(_activity(1))
    sink.emit(_activity(2))
    assert [item.sequence for item in sink.items] == [1, 2]


def test_fleet_activity_log_budget_is_half_the_local_archive_budget() -> None:
    assert FLEET_ACTIVITY_LOG_MAX_BYTES == ACTIVITY_LOG_MAX_BYTES // 2


def test_bounded_ndjson_preserves_untruncated_input_lines() -> None:
    line = _activity(1).model_dump_json() + " "

    assert bounded_ndjson([line], len(line.encode("utf-8")) + 1) == f"{line}\n"


def test_bounded_ndjson_appends_one_marker_from_the_first_overflowing_line() -> None:
    first = _activity(1).model_dump_json()
    overflow = _activity(2, summary="x" * 2000).model_dump_json()
    marker = _activity(2, kind="raw", summary="truncated", detail=None).model_dump_json()
    budget = len(first.encode("utf-8")) + len(marker.encode("utf-8")) + 2

    bounded = bounded_ndjson([first, overflow, "not-json"], budget)
    archived = [
        OperatorActivity.model_validate_json(line)
        for line in bounded.splitlines()
    ]

    assert len(bounded.encode("utf-8")) <= budget
    assert [item.sequence for item in archived] == [1, 2]
    assert archived[-1].kind == "raw"
    assert archived[-1].summary == "truncated"
    assert (
        sum(item.kind == "raw" and item.summary == "truncated" for item in archived)
        == 1
    )


@pytest.fixture
async def store(dialect_engine) -> WorkActivityStore:  # noqa: F811
    result = WorkActivityStore(engine=dialect_engine)
    await result.init()
    return result


@pytest.mark.asyncio
async def test_store_appends_reads_after_and_caps_per_run(store: WorkActivityStore) -> None:
    await store.append([_activity(1), _activity(2)])
    assert [item.sequence for item in await store.read("w", run_id="w:implement:1", project_id="p")] == [1, 2]
    assert [item.sequence for item in await store.read("w", run_id="w:implement:1", project_id="p", after=1)] == [2]
    await store.append([_activity(2)])
    assert len(await store.read("w", run_id="w:implement:1", project_id="p")) == 2
    await store.append([_activity(n) for n in range(3, ACTIVITY_ROW_CAP + 50)])
    rows = await store.read("w", run_id="w:implement:1", project_id="p", limit=10_000)
    assert len(rows) == ACTIVITY_ROW_CAP
    assert rows[-1].kind == "raw" and rows[-1].summary == "truncated"
    assert rows[-1].sequence == ACTIVITY_ROW_CAP


@pytest.mark.asyncio
async def test_store_prunes_old_rows_for_completed_work(store: WorkActivityStore) -> None:
    old = NOW.replace(year=2026, month=7)
    await store.append([_activity(1, at=old), _activity(2, at=NOW)])
    await store.append([_activity(1, work_id="w2", at=old)])
    pruned = await store.prune(project_id="p", completed_work_ids=("w",), older_than=NOW)
    assert pruned == 1
    assert [item.sequence for item in await store.read("w", run_id="w:implement:1", project_id="p")] == [2]
    assert len(await store.read("w2", run_id="w:implement:1", project_id="p")) == 1


@pytest.mark.asyncio
async def test_store_scopes_each_row_by_its_own_project(store: WorkActivityStore) -> None:
    assert len(await store.append([])) == 0
    await store.append([_activity(1), _activity(1, project_id="other")])
    assert [item.project_id for item in await store.read("w", run_id="w:implement:1", project_id="p")] == ["p"]
    assert [item.project_id for item in await store.read("w", run_id="w:implement:1", project_id="other")] == ["other"]


@pytest.mark.asyncio
async def test_store_round_trips_global_scope_activity(store: WorkActivityStore) -> None:
    item = _activity(1, project_id=None)
    assert len(await store.append([item])) == 1
    assert await store.read("w", run_id="w:implement:1", project_id=None) == [item]
    assert await store.read("w", run_id="w:implement:1", project_id="p") == []


@pytest.mark.asyncio
async def test_store_writes_one_marker_and_ignores_later_over_cap_batches(store: WorkActivityStore) -> None:
    assert len(await store.append([_activity(ACTIVITY_ROW_CAP + 5), _activity(ACTIVITY_ROW_CAP)])) == 1
    assert len(await store.append([_activity(ACTIVITY_ROW_CAP + 6)])) == 0
    rows = await store.read("w", run_id="w:implement:1", project_id="p", limit=10)
    assert [(item.sequence, item.kind, item.summary) for item in rows] == [(ACTIVITY_ROW_CAP, "raw", "truncated")]


@pytest.mark.asyncio
async def test_store_writes_one_marker_per_over_cap_run_in_same_batch(store: WorkActivityStore) -> None:
    inserted = await store.append(
        [
            _activity(ACTIVITY_ROW_CAP + 5, run_id="run-a"),
            _activity(ACTIVITY_ROW_CAP + 6, run_id="run-a"),
            _activity(ACTIVITY_ROW_CAP + 5, run_id="run-b"),
            _activity(ACTIVITY_ROW_CAP + 6, run_id="run-b"),
        ]
    )

    assert len(inserted) == 2
    run_a = await store.read("w", run_id="run-a", project_id="p", limit=10)
    run_b = await store.read("w", run_id="run-b", project_id="p", limit=10)
    assert [(item.sequence, item.kind, item.summary) for item in run_a] == [
        (ACTIVITY_ROW_CAP, "raw", "truncated")
    ]
    assert [(item.sequence, item.kind, item.summary) for item in run_b] == [
        (ACTIVITY_ROW_CAP, "raw", "truncated")
    ]


@pytest.mark.asyncio
async def test_store_refuses_sqlite_before_multi_row_returning_floor(
    dialect_engine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 1))

    if dialect_engine.dialect.name == "sqlite":
        with pytest.raises(
            RuntimeError,
            match="work_activity requires SQLite 3.35 or newer for multi-row RETURNING",
        ):
            WorkActivityStore(engine=dialect_engine)
    else:
        WorkActivityStore(engine=dialect_engine)


def _task_activity(
    work_id: str,
    run_id: str,
    sequence: int,
    *,
    source: str = "codex",
) -> OperatorActivity:
    return OperatorActivity(
        project_id="p",
        work_id=work_id,
        run_id=run_id,
        sequence=sequence,
        at=NOW,
        source=source,
        kind="message",
        summary=f"{work_id}/{run_id}/{sequence}",
    )


@pytest.mark.asyncio
async def test_read_activity_spans_runs_in_work_run_sequence_order(
    dialect_engine,  # noqa: F811
) -> None:
    store = WorkActivityStore(engine=dialect_engine)
    await store.init()
    await store.append(
        [
            _task_activity("w1", "w1:implement:2", 1),
            _task_activity("w1", "w1:implement:1", 2),
            _task_activity("w1", "w1:implement:1", 1),
            _task_activity("w2", "w2:review:1", 1),
        ]
    )

    page = await store.read_activity(project_id="p", work_ids=("w1", "w2"))

    assert [item.summary for item in page.items] == [
        "w1/w1:implement:1/1",
        "w1/w1:implement:1/2",
        "w1/w1:implement:2/1",
        "w2/w2:review:1/1",
    ]
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_read_activity_pages_by_cursor(dialect_engine) -> None:  # noqa: F811
    store = WorkActivityStore(engine=dialect_engine)
    await store.init()
    await store.append(
        [
            _task_activity("w1", "w1:implement:1", 1),
            _task_activity("w1", "w1:implement:1", 2),
            _task_activity("w1", "w1:review:1", 1),
            _task_activity("w1", "w1:review:1", 2),
        ]
    )

    first = await store.read_activity(project_id="p", work_ids=("w1",), limit=2)
    second = await store.read_activity(
        project_id="p", work_ids=("w1",), limit=2, after=first.next_cursor
    )

    assert [(item.run_id, item.sequence) for item in first.items] == [
        ("w1:implement:1", 1),
        ("w1:implement:1", 2),
    ]
    assert first.next_cursor is not None
    assert [(item.run_id, item.sequence) for item in second.items] == [
        ("w1:review:1", 1),
        ("w1:review:1", 2),
    ]


@pytest.mark.asyncio
async def test_read_activity_source_filter_advances_from_scanned_rows(
    dialect_engine,  # noqa: F811
) -> None:
    store = WorkActivityStore(engine=dialect_engine)
    await store.init()
    await store.append(
        [
            _task_activity("w1", "w1:implement:1", 1),
            _task_activity("w1", "w1:implement:1", 2),
            _task_activity("w1", "w1:implement:1", 3),
            _task_activity("w1", "w1:implement:1", 4),
            _task_activity("w1", "w1:implement:1", 5, source="verifier"),
        ]
    )

    pages = []
    cursor = None
    while True:
        page = await store.read_activity(
            project_id="p", work_ids=("w1",), source="verifier", after=cursor, limit=2
        )
        pages.append(page)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert len(pages) == 3
    assert [item.sequence for page in pages for item in page.items] == [5]


@pytest.mark.asyncio
async def test_read_activity_filters_by_run_and_source(dialect_engine) -> None:  # noqa: F811
    store = WorkActivityStore(engine=dialect_engine)
    await store.init()
    await store.append(
        [
            _task_activity("w1", "w1:implement:1", 1, source="codex"),
            _task_activity("w1", "w1:implement:1", 2, source="verifier"),
            _task_activity("w1", "w1:review:1", 1, source="claude"),
        ]
    )

    by_run = await store.read_activity(project_id="p", work_ids=("w1",), run_id="w1:review:1")
    by_source = await store.read_activity(project_id="p", work_ids=("w1",), source="verifier")

    assert [item.summary for item in by_run.items] == ["w1/w1:review:1/1"]
    assert [item.source for item in by_source.items] == ["verifier"]


@pytest.mark.asyncio
async def test_read_activity_with_no_works_is_empty(dialect_engine) -> None:  # noqa: F811
    store = WorkActivityStore(engine=dialect_engine)
    await store.init()

    page = await store.read_activity(project_id="p", work_ids=())

    assert page.items == ()
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_read_activity_is_project_scoped(dialect_engine) -> None:  # noqa: F811
    store = WorkActivityStore(engine=dialect_engine)
    await store.init()
    await store.append([_task_activity("w1", "w1:implement:1", 1)])

    page = await store.read_activity(project_id="q", work_ids=("w1",))

    assert page.items == ()
