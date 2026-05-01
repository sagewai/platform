# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""WorkerCostTrackerView — accumulates LLM-call costs per run_id."""
from __future__ import annotations

from sagewai.core.cost_tracker_view import WorkerCostTrackerView


def test_get_run_cost_zero_initially():
    v = WorkerCostTrackerView()
    assert v.get_run_cost_usd("r-1") is None


def test_record_then_get():
    v = WorkerCostTrackerView()
    v.record_llm_call(run_id="r-1", cost_usd=0.5)
    v.record_llm_call(run_id="r-1", cost_usd=0.25)
    assert v.get_run_cost_usd("r-1") == 0.75


def test_per_run_isolation():
    v = WorkerCostTrackerView()
    v.record_llm_call(run_id="r-1", cost_usd=1.0)
    v.record_llm_call(run_id="r-2", cost_usd=0.1)
    assert v.get_run_cost_usd("r-1") == 1.0
    assert v.get_run_cost_usd("r-2") == 0.1


def test_clear_run():
    v = WorkerCostTrackerView()
    v.record_llm_call(run_id="r-1", cost_usd=1.0)
    v.clear_run("r-1")
    assert v.get_run_cost_usd("r-1") is None
