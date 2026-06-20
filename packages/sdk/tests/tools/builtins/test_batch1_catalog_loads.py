# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Assert every batch-1 catalog entry loads and its entrypoint resolves."""
from sagewai.tools import registry
from sagewai.tools.executors.sdk import _resolve


BATCH_1_IDS = {
    "diff_text", "structured_write", "record_result", "progress_track",
    "request_approval", "web_scrape", "web_search", "pdf_parse",
    "content_translate", "quiz_generate", "notify",
}


def test_all_batch1_entries_in_none_tier():
    registry._reset()
    registry.load()
    none_tier_ids = {e.id for e in registry.list_by_tier("none")}
    missing = BATCH_1_IDS - none_tier_ids
    assert not missing, f"missing batch-1 entries in none tier: {missing}"


def test_all_batch1_entrypoints_resolve():
    registry._reset()
    registry.load()
    for tid in BATCH_1_IDS:
        entry = registry.lookup(tid)
        assert entry.kind == "sdk"
        callable_obj = _resolve(entry.exec_["sdk"]["entrypoint"])
        assert callable(callable_obj), f"{tid} entrypoint did not resolve"
