# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Line parsers for the native CLIs' event streams.

Pinned integrations: codex-cli 0.147 (`codex exec --json`, one JSON object per line with
``type`` in ``thread.started``, ``turn.started``, ``item.started``, ``item.updated``,
``item.completed``, ``turn.completed``, ``error``) and Claude Code 2.1
(`--output-format stream-json --verbose`, objects with ``type`` in ``system``,
``assistant``, ``user``, ``result``). Unknown or malformed lines become ``raw`` events.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sagewai.work.activity import ActivityKind, ActivitySource, OperatorActivity


class ActivityCounter:
    """Hands out the per-run activity sequence."""

    def __init__(self, *, project_id: str | None, work_id: str, run_id: str) -> None:
        self.project_id = project_id
        self.work_id = work_id
        self.run_id = run_id
        self._sequence = 0

    def next(
        self, *, source: ActivitySource, kind: ActivityKind, summary: str, detail: str | None = None, **usage: Any
    ) -> OperatorActivity:
        self._sequence += 1
        return OperatorActivity(
            project_id=self.project_id,
            work_id=self.work_id,
            run_id=self.run_id,
            sequence=self._sequence,
            at=datetime.now(timezone.utc),
            source=source,
            kind=kind,
            summary=summary,
            detail=detail,
            **usage,
        )


def _load(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def parse_codex_json_line(line: str, counter: ActivityCounter) -> list[OperatorActivity]:
    if not line.strip():
        return []
    payload = _load(line)
    if payload is None:
        return [counter.next(source="codex", kind="raw", summary=line)]
    kind = payload.get("type", "")
    item = payload.get("item") or {}
    item_type = item.get("type", "")
    if kind in {"item.started", "item.completed"} and item_type == "command_execution":
        if kind == "item.started":
            return [counter.next(source="codex", kind="command", summary=str(item.get("command", "")))]
        return [
            counter.next(
                source="codex",
                kind="tool_result",
                summary=f"{item.get('command', '')} -> exit {item.get('exit_code')}",
                detail=str(item.get("aggregated_output", "")) or None,
            )
        ]
    if kind == "item.completed" and item_type == "file_change":
        paths = ", ".join(str(change.get("path", "")) for change in item.get("changes", ()))
        return [counter.next(source="codex", kind="file_change", summary=paths)]
    if kind == "item.completed" and item_type in {"agent_message", "assistant_message"}:
        return [counter.next(source="codex", kind="message", summary=str(item.get("text", "")))]
    if kind == "item.completed" and item_type == "reasoning":
        return [counter.next(source="codex", kind="reasoning", summary=str(item.get("text", "")))]
    if kind == "item.completed" and item_type in {"mcp_tool_call", "web_search"}:
        return [counter.next(source="codex", kind="tool_call", summary=json.dumps(item, sort_keys=True))]
    if kind == "turn.completed":
        usage = payload.get("usage") or {}
        return [
            counter.next(
                source="codex",
                kind="usage",
                summary="turn completed",
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
            )
        ]
    if kind == "error":
        return [counter.next(source="codex", kind="error", summary=str(payload.get("message", line)))]
    if kind in {"thread.started", "turn.started", "item.updated"}:
        return []
    return [counter.next(source="codex", kind="raw", summary=line)]


def claude_result_from_line(line: str) -> dict[str, Any] | None:
    payload = _load(line)
    return payload if payload is not None and payload.get("type") == "result" else None


def parse_claude_stream_line(line: str, counter: ActivityCounter) -> list[OperatorActivity]:
    if not line.strip():
        return []
    payload = _load(line)
    if payload is None:
        return [counter.next(source="claude", kind="raw", summary=line)]
    kind = payload.get("type", "")
    if kind == "assistant":
        events = []
        for block in (payload.get("message") or {}).get("content", ()):
            block_type = block.get("type")
            if block_type == "text":
                events.append(counter.next(source="claude", kind="message", summary=block.get("text", "")))
            elif block_type == "tool_use":
                events.append(
                    counter.next(
                        source="claude",
                        kind="tool_call",
                        summary=str(block.get("name", "")),
                        detail=json.dumps(block.get("input", {}), sort_keys=True),
                    )
                )
            elif block_type == "thinking":
                events.append(counter.next(source="claude", kind="reasoning", summary=block.get("thinking", "")))
        return events
    if kind == "user":
        events = []
        for block in (payload.get("message") or {}).get("content", ()):
            if block.get("type") == "tool_result":
                content = block.get("content")
                text = content if isinstance(content, str) else json.dumps(content, sort_keys=True)
                events.append(counter.next(source="claude", kind="tool_result", summary=text, detail=text))
        return events
    if kind == "result":
        usage = payload.get("usage") or {}
        return [
            counter.next(
                source="claude",
                kind="error" if payload.get("is_error") else "usage",
                summary=str(payload.get("subtype", "result")),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cost_usd=payload.get("total_cost_usd"),
            )
        ]
    if kind == "system":
        return []
    return [counter.next(source="claude", kind="raw", summary=line)]


__all__ = ["ActivityCounter", "claude_result_from_line", "parse_claude_stream_line", "parse_codex_json_line"]
