# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""AdminStateFile carries sandbox_pool config block."""
from pathlib import Path

from sagewai.admin.state_file import AdminStateFile


def test_state_file_default_sandbox_pool_returns_empty(tmp_path: Path) -> None:
    sf = AdminStateFile(path=tmp_path / "state.json")
    cfg = sf.get_sandbox_pool_config()
    assert cfg == {}


def test_state_file_set_and_get_sandbox_pool(tmp_path: Path) -> None:
    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.set_sandbox_pool_config(
        {"pool_max_warm_per_tuple": 8, "pool_disable_warm_reuse": True}
    )
    cfg = sf.get_sandbox_pool_config()
    assert cfg["pool_max_warm_per_tuple"] == 8
    assert cfg["pool_disable_warm_reuse"] is True
