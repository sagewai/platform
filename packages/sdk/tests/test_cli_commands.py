# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for CLI admin-backed command groups.

Each command calls the admin API via httpx and formats the result.
We mock httpx responses to test the CLI output without a running server.
"""

from __future__ import annotations

import json

from click.testing import CliRunner
from pytest_httpx import HTTPXMock

from sagewai.cli import cli

runner = CliRunner()

ADMIN_BASE = "http://localhost:8000"


# ---------------------------------------------------------------------------
# agent commands
# ---------------------------------------------------------------------------


class TestAgentCommands:
    def test_agent_api_list(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/admin/agents",
            json=[
                {"name": "scout", "status": "idle", "model": "gpt-4o"},
                {"name": "writer", "status": "running", "model": "claude-3"},
            ],
        )
        result = runner.invoke(cli, ["agent", "api-list"])
        assert result.exit_code == 0
        assert "scout" in result.output
        assert "writer" in result.output

    def test_agent_api_list_json(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/admin/agents",
            json=[{"name": "scout", "status": "idle", "model": "gpt-4o"}],
        )
        result = runner.invoke(cli, ["agent", "api-list", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_agent_api_show(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/admin/agents/scout",
            json={
                "name": "scout",
                "status": "idle",
                "model": "gpt-4o",
                "total_runs": 5,
                "max_iterations": 10,
                "capabilities": ["search"],
                "tools": ["web_search"],
            },
        )
        result = runner.invoke(cli, ["agent", "api-show", "scout"])
        assert result.exit_code == 0
        assert "scout" in result.output
        assert "gpt-4o" in result.output


# ---------------------------------------------------------------------------
# run commands
# ---------------------------------------------------------------------------


class TestRunCommands:
    def test_run_api_list(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/admin/runs",
            json=[
                {
                    "run_id": "abc123",
                    "agent_name": "scout",
                    "status": "completed",
                    "total_tokens": 500,
                },
            ],
        )
        result = runner.invoke(cli, ["run", "api-list"])
        assert result.exit_code == 0
        assert "abc123" in result.output
        assert "scout" in result.output

    def test_run_api_show(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/admin/runs/abc123",
            json={
                "run_id": "abc123",
                "agent_name": "scout",
                "status": "completed",
                "total_tokens": 500,
                "input_text": "Hello",
                "output_text": "World",
                "steps": [],
                "tool_calls": [],
            },
        )
        result = runner.invoke(cli, ["run", "api-show", "abc123"])
        assert result.exit_code == 0
        assert "abc123" in result.output

    def test_run_pause(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/admin/runs/abc123/pause",
            method="POST",
            json={"status": "paused"},
        )
        result = runner.invoke(cli, ["run", "pause", "abc123"])
        assert result.exit_code == 0

    def test_run_resume(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/admin/runs/abc123/resume",
            method="POST",
            json={"status": "running"},
        )
        result = runner.invoke(cli, ["run", "resume", "abc123"])
        assert result.exit_code == 0

    def test_run_cancel(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/admin/runs/abc123/cancel",
            method="POST",
            json={"status": "cancelled"},
        )
        result = runner.invoke(cli, ["run", "cancel", "abc123"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# session commands
# ---------------------------------------------------------------------------


class TestSessionCommands:
    def test_session_list(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/admin/sessions",
            json=[
                {"session_id": "sess-1", "agent_name": "scout", "message_count": 3},
            ],
        )
        result = runner.invoke(cli, ["session", "list"])
        assert result.exit_code == 0
        assert "sess-1" in result.output

    def test_session_show(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/admin/sessions/sess-1",
            json={
                "session_id": "sess-1",
                "agent_name": "scout",
                "message_count": 3,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        result = runner.invoke(cli, ["session", "show", "sess-1"])
        assert result.exit_code == 0
        assert "sess-1" in result.output


# ---------------------------------------------------------------------------
# workflow commands
# ---------------------------------------------------------------------------


class TestWorkflowCommands:
    def test_workflow_list_templates(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/workflows/templates",
            json=[
                {"name": "Sequential Pipeline", "description": "Agents run one after another"},
            ],
        )
        result = runner.invoke(cli, ["workflow", "list-templates"])
        assert result.exit_code == 0
        assert "Sequential Pipeline" in result.output

    def test_workflow_validate(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/workflows/validate",
            method="POST",
            json={"valid": True, "name": "my-workflow", "agents": []},
        )
        result = runner.invoke(
            cli, ["workflow", "validate", "--yaml", "name: my-workflow\nagents: {}"]
        )
        assert result.exit_code == 0
        assert "valid" in result.output.lower() or "my-workflow" in result.output


# ---------------------------------------------------------------------------
# strategy commands
# ---------------------------------------------------------------------------


class TestStrategyCommands:
    def test_strategy_list(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/strategies/list",
            json=[
                {"name": "react", "description": "ReAct loop"},
                {"name": "tot", "description": "Tree of Thought"},
            ],
        )
        result = runner.invoke(cli, ["strategy", "list"])
        assert result.exit_code == 0
        assert "react" in result.output
        assert "tot" in result.output


# ---------------------------------------------------------------------------
# budget commands
# ---------------------------------------------------------------------------


class TestBudgetCommands:
    def test_budget_list(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/budget/limits",
            json=[
                {
                    "agent_name": "scout",
                    "daily_limit_usd": 5.0,
                    "monthly_limit_usd": 100.0,
                },
            ],
        )
        result = runner.invoke(cli, ["budget", "api-list"])
        assert result.exit_code == 0
        assert "scout" in result.output

    def test_budget_set(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/budget/limits",
            method="POST",
            json={"agent_name": "scout", "daily_limit_usd": 10.0},
        )
        result = runner.invoke(cli, ["budget", "set", "scout", "--daily", "10.0"])
        assert result.exit_code == 0

    def test_budget_remove(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/budget/limits/scout",
            method="DELETE",
            json={"deleted": True},
        )
        result = runner.invoke(cli, ["budget", "remove", "scout"])
        assert result.exit_code == 0

    def test_budget_status(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/budget/status/scout",
            json={
                "agent_name": "scout",
                "daily_spend": 2.50,
                "monthly_spend": 45.0,
                "daily_limit_usd": 5.0,
                "monthly_limit_usd": 100.0,
            },
        )
        result = runner.invoke(cli, ["budget", "status", "scout"])
        assert result.exit_code == 0
        assert "scout" in result.output


# ---------------------------------------------------------------------------
# safety commands
# ---------------------------------------------------------------------------


class TestSafetyCommands:
    def test_safety_guardrails(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/guardrails/configs",
            json=[
                {"agent_name": "scout", "guardrail_type": "pii", "enabled": True},
                {"agent_name": "scout", "guardrail_type": "hallucination", "enabled": False},
            ],
        )
        result = runner.invoke(cli, ["safety", "guardrails"])
        assert result.exit_code == 0
        assert "scout" in result.output
        assert "pii" in result.output

    def test_safety_guardrails_json(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/guardrails/configs",
            json=[{"agent_name": "scout", "guardrail_type": "pii", "enabled": True}],
        )
        result = runner.invoke(cli, ["safety", "guardrails", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert len(parsed) == 1

    def test_safety_guardrails_by_agent(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/guardrails/configs?agent_name=scout",
            json=[{"agent_name": "scout", "guardrail_type": "pii", "enabled": True}],
        )
        result = runner.invoke(cli, ["safety", "guardrails", "--agent", "scout"])
        assert result.exit_code == 0

    def test_safety_set(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/guardrails/configs/scout",
            method="PUT",
            json={"agent_name": "scout", "guardrail_type": "pii", "enabled": True},
        )
        result = runner.invoke(cli, ["safety", "set", "scout", "--pii"])
        assert result.exit_code == 0
        assert "pii" in result.output

    def test_safety_set_no_flags(self):
        result = runner.invoke(cli, ["safety", "set", "scout"])
        assert result.exit_code == 0
        assert "No guardrail flags" in result.output

    def test_safety_audit(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/audit/events?limit=20",
            json={
                "events": [
                    {
                        "id": 1,
                        "agent_name": "scout",
                        "event_type": "pii_detected",
                        "detail": "EMAIL found",
                        "created_at": "2026-01-01T00:00:00",
                    },
                ],
                "total": 1,
                "limit": 20,
                "offset": 0,
            },
        )
        result = runner.invoke(cli, ["safety", "audit"])
        assert result.exit_code == 0
        assert "pii_detected" in result.output

    def test_safety_audit_json(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/audit/events?limit=20",
            json={"events": [], "total": 0, "limit": 20, "offset": 0},
        )
        result = runner.invoke(cli, ["safety", "audit", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "events" in parsed

    def test_safety_export_json(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/audit/export?format=json",
            json=[{"id": 1, "event_type": "pii_detected"}],
        )
        result = runner.invoke(cli, ["safety", "export"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# memory commands
# ---------------------------------------------------------------------------


class TestMemoryCommands:
    def test_memory_vector_stats(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/memory/vector/stats",
            json={"status": "active", "documents": 42, "backend": "VectorMemory"},
        )
        result = runner.invoke(cli, ["memory", "vector-stats"])
        assert result.exit_code == 0
        assert "42" in result.output

    def test_memory_vector_stats_json(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/memory/vector/stats",
            json={"status": "active", "documents": 42, "backend": "VectorMemory"},
        )
        result = runner.invoke(cli, ["memory", "vector-stats", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["documents"] == 42

    def test_memory_vector_search(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/memory/vector/search",
            method="POST",
            json={
                "query": "python",
                "results": [{"content": "Python is a language", "rank": 1}],
                "count": 1,
            },
        )
        result = runner.invoke(cli, ["memory", "vector-search", "python"])
        assert result.exit_code == 0
        assert "Python" in result.output

    def test_memory_vector_ingest(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/memory/vector/ingest",
            method="POST",
            json={"status": "ingested"},
        )
        result = runner.invoke(cli, ["memory", "vector-ingest", "some document text"])
        assert result.exit_code == 0
        assert "ingested" in result.output

    def test_memory_graph_stats(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/memory/graph/stats",
            json={"status": "active", "entities": 10, "relations": 5, "backend": "GraphMemory"},
        )
        result = runner.invoke(cli, ["memory", "graph-stats"])
        assert result.exit_code == 0
        assert "10" in result.output
        assert "5" in result.output

    def test_memory_graph_query(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/memory/graph/query",
            method="POST",
            json={"query": "sagewai", "results": [], "count": 0},
        )
        result = runner.invoke(cli, ["memory", "graph-query", "sagewai"])
        assert result.exit_code == 0

    def test_memory_graph_entity(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/memory/graph/entity/Python",
            json={"name": "Python", "metadata": {"type": "language"}},
        )
        result = runner.invoke(cli, ["memory", "graph-entity", "Python"])
        assert result.exit_code == 0
        assert "Python" in result.output


# ---------------------------------------------------------------------------
# eval API commands (M3)
# ---------------------------------------------------------------------------


class TestEvalAPICommands:
    def test_eval_datasets(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/eval/datasets",
            json=[
                {"id": 1, "name": "safety-v1", "case_count": 10, "created_at": "2026-03-01"},
            ],
        )
        result = runner.invoke(cli, ["eval", "datasets"])
        assert result.exit_code == 0
        assert "safety-v1" in result.output

    def test_eval_datasets_empty(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=f"{ADMIN_BASE}/api/v1/eval/datasets", json=[])
        result = runner.invoke(cli, ["eval", "datasets"])
        assert result.exit_code == 0
        assert "No eval datasets" in result.output

    def test_eval_runs(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/eval/runs",
            json=[
                {
                    "id": 1,
                    "agent_name": "scout",
                    "model": "gpt-4o-mini",
                    "pass_rate": 0.8,
                    "passed": 4,
                    "total_cases": 5,
                },
            ],
        )
        result = runner.invoke(cli, ["eval", "runs"])
        assert result.exit_code == 0
        assert "scout" in result.output
        assert "80%" in result.output

    def test_eval_delete(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/eval/datasets/1",
            method="DELETE",
            json={"deleted": "1"},
        )
        result = runner.invoke(cli, ["eval", "delete", "1"])
        assert result.exit_code == 0
        assert "Deleted" in result.output


# ---------------------------------------------------------------------------
# MCP API commands (M3)
# ---------------------------------------------------------------------------


class TestMcpAPICommands:
    def test_mcp_api_servers(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/mcp/servers",
            json=[
                {"name": "knowledge", "path": "mcp-servers/knowledge", "status": "configured"},
            ],
        )
        result = runner.invoke(cli, ["mcp", "api-servers"])
        assert result.exit_code == 0
        assert "knowledge" in result.output

    def test_mcp_call(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/mcp/call",
            method="POST",
            json={"tool_name": "search", "arguments": {}, "result": {"answer": "42"}},
        )
        result = runner.invoke(cli, ["mcp", "call", "python -m mcp_knowledge", "--tool", "search"])
        assert result.exit_code == 0
        assert "search" in result.output
        assert "42" in result.output


# ---------------------------------------------------------------------------
# model commands (M3)
# ---------------------------------------------------------------------------


class TestModelCommands:
    def test_model_list(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/model-router/models",
            json=["gpt-4o", "claude-3-sonnet", "gemini-pro"],
        )
        result = runner.invoke(cli, ["model", "list"])
        assert result.exit_code == 0
        assert "gpt-4o" in result.output
        assert "claude-3-sonnet" in result.output

    def test_model_rules(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/model-router/rules",
            json=[
                {
                    "name": "short_query",
                    "target_model": "gpt-4o-mini",
                    "condition": "len < 50",
                    "description": "Short queries use mini model",
                },
            ],
        )
        result = runner.invoke(cli, ["model", "rules"])
        assert result.exit_code == 0
        assert "short_query" in result.output

    def test_model_test(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/model-router/test",
            method="POST",
            json={
                "query": "Hi",
                "context": {},
                "selected_model": "gpt-4o-mini",
                "default_model": "gpt-4o",
            },
        )
        result = runner.invoke(cli, ["model", "test", "Hi"])
        assert result.exit_code == 0
        assert "gpt-4o-mini" in result.output


# ---------------------------------------------------------------------------
# prompt commands (M3)
# ---------------------------------------------------------------------------


class TestPromptCommands:
    def test_prompt_list(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/prompts/logs?limit=20",
            json=[
                {
                    "log_id": "log-abc123",
                    "agent_name": "scout",
                    "model": "gpt-4o",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cost_usd": 0.002,
                },
            ],
        )
        result = runner.invoke(cli, ["prompt", "list"])
        assert result.exit_code == 0
        assert "scout" in result.output

    def test_prompt_show(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/prompts/logs/log-abc",
            json={
                "log_id": "log-abc",
                "agent_name": "scout",
                "model": "gpt-4o",
                "duration_ms": 500,
                "cost_usd": 0.002,
                "prompt_messages": [{"role": "user", "content": "Hello"}],
                "response_message": {"role": "assistant", "content": "Hi there"},
            },
        )
        result = runner.invoke(cli, ["prompt", "show", "log-abc"])
        assert result.exit_code == 0
        assert "scout" in result.output
        assert "Hello" in result.output

    def test_prompt_replay(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/prompts/replay",
            method="POST",
            json={
                "original_model": "gpt-4o",
                "replay_model": "claude-3",
                "original_response": {"role": "assistant", "content": "Original answer"},
                "replay_response": {"role": "assistant", "content": "Replay answer"},
            },
        )
        result = runner.invoke(cli, ["prompt", "replay", "log-abc", "--model", "claude-3"])
        assert result.exit_code == 0
        assert "Original answer" in result.output
        assert "Replay answer" in result.output


# ---------------------------------------------------------------------------
# token commands (M3)
# ---------------------------------------------------------------------------


class TestTokenCommands:
    def test_token_list(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/tokens/",
            json=[
                {
                    "token_id": "tok-abc123456789",
                    "agent_name": "scout",
                    "scopes": ["chat"],
                    "status": "active",
                },
            ],
        )
        result = runner.invoke(cli, ["token", "list"])
        assert result.exit_code == 0
        assert "scout" in result.output

    def test_token_create(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/tokens/",
            method="POST",
            json={
                "token": "sage_tok_xyz",
                "token_id": "tok-new",
                "agent_name": "scout",
                "scopes": ["chat"],
                "expires_in_seconds": 86400,
            },
        )
        result = runner.invoke(cli, ["token", "create", "--agent", "scout"])
        assert result.exit_code == 0
        assert "sage_tok_xyz" in result.output
        assert "scout" in result.output

    def test_token_revoke(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/tokens/tok-abc/revoke",
            method="POST",
            json={"token_id": "tok-abc", "status": "revoked"},
        )
        result = runner.invoke(cli, ["token", "revoke", "tok-abc"])
        assert result.exit_code == 0
        assert "revoked" in result.output

    def test_token_delete(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{ADMIN_BASE}/api/v1/tokens/tok-abc",
            method="DELETE",
            json={"token_id": "tok-abc", "deleted": True},
        )
        result = runner.invoke(cli, ["token", "delete", "tok-abc"])
        assert result.exit_code == 0
        assert "deleted" in result.output
