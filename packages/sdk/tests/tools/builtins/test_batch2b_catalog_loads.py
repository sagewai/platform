# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Assert every batch-2b catalog change is wired correctly."""
from sagewai.tools import registry


BATCH_2B_NEW_IDS = {"hubspot_api", "greenhouse_api", "maps_route"}


def test_new_batch2b_entries_in_api_key_tier():
    registry._reset()
    registry.load()
    ids = {e.id for e in registry.list_by_tier("api_key")}
    missing = BATCH_2B_NEW_IDS - ids
    assert not missing, f"missing batch-2b entries in api_key tier: {missing}"


def test_new_batch2b_entries_declare_credential_fields():
    registry._reset()
    registry.load()
    for tid in BATCH_2B_NEW_IDS:
        creds = registry.required_credentials(tid)
        assert creds, f"{tid} must declare credential_fields"


def test_github_has_new_write_ops():
    registry._reset()
    registry.load()
    entry = registry.lookup("github")
    ops = set(entry.exec_["http"]["operations"].keys())
    expected_new = {"create_issue", "create_comment", "create_pull_request", "search_code"}
    missing = expected_new - ops
    assert not missing, f"github missing extended ops: {missing}"


def test_github_has_git_write_scope():
    registry._reset()
    registry.load()
    scopes = registry.scopes_for("github")
    assert "git.write" in scopes, f"github missing git.write scope; got {sorted(scopes)}"


def test_github_still_has_existing_get_repo_op():
    """Regression check: extending github.yaml must not remove get_repo."""
    registry._reset()
    registry.load()
    entry = registry.lookup("github")
    assert "get_repo" in entry.exec_["http"]["operations"]
