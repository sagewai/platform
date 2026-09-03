# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Activity parsers are pinned to recorded CLI fixtures; unknown lines become raw events."""

from __future__ import annotations

import json
from pathlib import Path

from sagewai.work.activity_parsers import (
    ActivityCounter,
    parse_claude_stream_line,
    parse_codex_json_line,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _parse(lines: list[str], parser) -> list:
    counter = ActivityCounter(project_id="p", work_id="w", run_id="w:implement:1")
    events = []
    for line in lines:
        events.extend(parser(line, counter))
    return events


def test_codex_json_stream_yields_messages_usage_and_raw_for_unknown() -> None:
    lines = (FIXTURES / "codex-exec.jsonl").read_text().splitlines()
    events = _parse(lines, parse_codex_json_line)
    kinds = [event.kind for event in events]
    assert "message" in kinds
    assert kinds.count("raw") <= 2
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert all(event.source == "codex" for event in events)
    usage = [event for event in events if event.kind == "usage"]
    assert usage and usage[-1].input_tokens is not None and usage[-1].output_tokens is not None


def test_activity_counter_accepts_global_scope() -> None:
    counter = ActivityCounter(project_id=None, work_id="w", run_id="r")
    event = parse_codex_json_line("not json", counter)[0]
    assert event.project_id is None


def test_codex_tool_and_command_lines_map_to_their_kinds() -> None:
    counter = ActivityCounter(project_id="p", work_id="w", run_id="r")
    command = json.dumps(
        {"type": "item.started", "item": {"type": "command_execution", "command": "ls -la", "id": "c1"}}
    )
    assert [event.kind for event in parse_codex_json_line(command, counter)] == ["command"]
    result = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "ls -la",
                "aggregated_output": "a\nb",
                "exit_code": 0,
                "id": "c1",
            },
        }
    )
    assert [event.kind for event in parse_codex_json_line(result, counter)] == ["tool_result"]
    change = json.dumps(
        {"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "a.py", "kind": "update"}], "id": "f1"}}
    )
    assert [event.kind for event in parse_codex_json_line(change, counter)] == ["file_change"]
    assert [event.kind for event in parse_codex_json_line("not json", counter)] == ["raw"]
    assert parse_codex_json_line("", counter) == []
    reasoning = json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "thinking", "id": "r1"}})
    assert [event.kind for event in parse_codex_json_line(reasoning, counter)] == ["reasoning"]
    for item_type in ("mcp_tool_call", "web_search"):
        line = json.dumps({"type": "item.completed", "item": {"type": item_type, "id": "t1", "query": "x"}})
        assert [event.kind for event in parse_codex_json_line(line, counter)] == ["tool_call"]
    error = json.dumps({"type": "error", "message": "rate limited"})
    errors = parse_codex_json_line(error, counter)
    assert [(event.kind, event.summary) for event in errors] == [("error", "rate limited")]
    assert [event.kind for event in parse_codex_json_line(json.dumps({"type": "turn.started"}), counter)] == []


def test_claude_stream_yields_messages_tool_calls_and_final_usage() -> None:
    lines = (FIXTURES / "claude-stream.jsonl").read_text().splitlines()
    events = _parse(lines, parse_claude_stream_line)
    assert all(event.source == "claude" for event in events)
    kinds = {event.kind for event in events}
    assert {"message", "tool_call", "tool_result", "usage"} <= kinds
    final = [event for event in events if event.kind == "usage"]
    assert final and final[-1].cost_usd is not None


def test_claude_synthetic_lines_map_thinking_errors_and_unknowns() -> None:
    counter = ActivityCounter(project_id="p", work_id="w", run_id="r")
    thinking = json.dumps({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hmm"}]}})
    assert [event.kind for event in parse_claude_stream_line(thinking, counter)] == ["reasoning"]
    failed = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "usage": {"input_tokens": 1, "output_tokens": 0}})
    assert [(event.kind, event.summary) for event in parse_claude_stream_line(failed, counter)] == [("error", "error_during_execution")]
    assert [event.kind for event in parse_claude_stream_line("not json", counter)] == ["raw"]
    assert parse_claude_stream_line("", counter) == []
    assert parse_claude_stream_line(json.dumps({"type": "system", "subtype": "init"}), counter) == []


def test_claude_string_assistant_content_becomes_raw() -> None:
    counter = ActivityCounter(project_id="p", work_id="w", run_id="r")
    line = json.dumps({"type": "assistant", "message": {"content": "plain string"}})

    events = parse_claude_stream_line(line, counter)

    assert [(event.kind, event.summary) for event in events] == [("raw", line)]


def test_claude_non_list_user_content_becomes_raw() -> None:
    counter = ActivityCounter(project_id="p", work_id="w", run_id="r")
    line = json.dumps(
        {"type": "user", "message": {"content": {"type": "tool_result", "content": "done"}}}
    )

    events = parse_claude_stream_line(line, counter)

    assert [(event.kind, event.summary) for event in events] == [("raw", line)]


def test_claude_non_mapping_result_usage_becomes_raw() -> None:
    counter = ActivityCounter(project_id="p", work_id="w", run_id="r")
    line = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "usage": [{"input_tokens": 5}],
            "total_cost_usd": 0.01,
            "structured_output": {"project_id": "p", "work_id": "w", "run_id": "r"},
        }
    )

    events = parse_claude_stream_line(line, counter)

    assert [(event.kind, event.summary) for event in events] == [("raw", line)]


def test_claude_result_line_exposes_structured_output() -> None:
    from sagewai.work.activity_parsers import claude_result_from_line

    lines = (FIXTURES / "claude-stream.jsonl").read_text().splitlines()
    result = next(item for item in (claude_result_from_line(line) for line in lines) if item is not None)
    assert result["type"] == "result"
    assert "structured_output" in result or "result" in result
