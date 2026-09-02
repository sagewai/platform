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

from datetime import datetime, timezone

import pytest

from sagewai.work.activity import (
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
    assert await store.append([]) == 0
    await store.append([_activity(1), _activity(1, project_id="other")])
    assert [item.project_id for item in await store.read("w", run_id="w:implement:1", project_id="p")] == ["p"]
    assert [item.project_id for item in await store.read("w", run_id="w:implement:1", project_id="other")] == ["other"]


@pytest.mark.asyncio
async def test_store_writes_one_marker_and_ignores_later_over_cap_batches(store: WorkActivityStore) -> None:
    assert await store.append([_activity(ACTIVITY_ROW_CAP + 5), _activity(ACTIVITY_ROW_CAP)]) == 1
    assert await store.append([_activity(ACTIVITY_ROW_CAP + 6)]) == 0
    rows = await store.read("w", run_id="w:implement:1", project_id="p", limit=10)
    assert [(item.sequence, item.kind, item.summary) for item in rows] == [(ACTIVITY_ROW_CAP, "raw", "truncated")]
