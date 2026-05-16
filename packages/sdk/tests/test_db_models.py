# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for SQLAlchemy model definitions."""

from __future__ import annotations

from sqlalchemy import inspect


def test_agent_run_table_name():
    from sagewai.db.models import AgentRun

    assert AgentRun.__tablename__ == "agent_runs"


def test_agent_run_columns():
    from sagewai.db.models import AgentRun

    mapper = inspect(AgentRun)
    col_names = {c.key for c in mapper.column_attrs}
    expected = {
        "run_id",
        "agent_name",
        "status",
        "input_text",
        "output_text",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "model",
        "duration_ms",
        "tool_calls",
        "metadata_",
        "started_at",
        "completed_at",
        "error",
        "checkpoint_run_id",
        "created_at",
    }
    assert expected.issubset(col_names)


def test_agent_run_primary_key():
    from sagewai.db.models import AgentRun

    mapper = inspect(AgentRun)
    pk_cols = [c.name for c in mapper.primary_key]
    assert pk_cols == ["run_id"]


def test_prompt_log_table_name():
    from sagewai.db.models import PromptLog

    assert PromptLog.__tablename__ == "prompt_logs"


def test_prompt_log_columns():
    from sagewai.db.models import PromptLog

    mapper = inspect(PromptLog)
    col_names = {c.key for c in mapper.column_attrs}
    expected = {
        "log_id",
        "run_id",
        "agent_name",
        "step_index",
        "model",
        "prompt_messages",
        "response_message",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "duration_ms",
        "strategy",
        "metadata_",
        "created_at",
    }
    assert expected.issubset(col_names)


def test_prompt_log_foreign_key():
    from sagewai.db.models import PromptLog

    mapper = inspect(PromptLog)
    run_id_col = mapper.columns["run_id"]
    fk_targets = [fk.target_fullname for fk in run_id_col.foreign_keys]
    assert "agent_runs.run_id" in fk_targets


def test_base_metadata_has_both_tables():
    from sagewai.db.models import Base

    table_names = set(Base.metadata.tables.keys())
    assert "agent_runs" in table_names
    assert "prompt_logs" in table_names
