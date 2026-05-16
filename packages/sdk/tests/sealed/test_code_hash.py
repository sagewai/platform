# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
from sagewai.core.state import DurableWorkflow
from sagewai.sealed.replay import compute_code_hash


async def _noop(x: str) -> str:
    return x


def _wf(step_names: list[str]) -> DurableWorkflow:
    wf = DurableWorkflow(name="t")
    for n in step_names:
        wf.step(n)(_noop)
    return wf


def test_code_hash_stable_for_same_step_list():
    assert compute_code_hash(_wf(["a", "b"])) == compute_code_hash(_wf(["a", "b"]))


def test_code_hash_changes_when_step_added():
    assert compute_code_hash(_wf(["a"])) != compute_code_hash(_wf(["a", "b"]))


def test_code_hash_changes_when_step_renamed():
    assert compute_code_hash(_wf(["a"])) != compute_code_hash(_wf(["A"]))


def test_code_hash_changes_when_step_reordered():
    assert compute_code_hash(_wf(["a", "b"])) != compute_code_hash(_wf(["b", "a"]))


def test_code_hash_is_hex_sha256():
    h = compute_code_hash(_wf(["a"]))
    assert len(h) == 64
    int(h, 16)  # valid hex
