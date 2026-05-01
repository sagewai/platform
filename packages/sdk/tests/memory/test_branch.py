# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for MemoryBranch namespace helper."""

from sagewai.memory.branch import MemoryBranch


def test_branch_prefixes_namespace():
    b = MemoryBranch(mission_id="m-123")
    assert b.scoped("semantic") == "m-123/semantic"


def test_branch_default_root_is_global():
    b = MemoryBranch.global_root()
    assert b.scoped("semantic") == "_global/semantic"


def test_two_branches_isolate_namespaces():
    a = MemoryBranch(mission_id="a")
    b = MemoryBranch(mission_id="b")
    assert a.scoped("preferences") != b.scoped("preferences")
