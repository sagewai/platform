# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""SandboxConfig pool sizing knobs."""
from sagewai.sandbox.models import SandboxConfig


def test_pool_config_defaults() -> None:
    cfg = SandboxConfig()
    assert cfg.pool_max_warm_per_tuple == 4
    assert cfg.pool_max_warm_global == 16
    assert cfg.pool_idle_timeout_s == 600
    assert cfg.pool_reap_interval_s == 60
    assert cfg.pool_disable_warm_reuse is False


def test_pool_config_override() -> None:
    cfg = SandboxConfig(pool_max_warm_per_tuple=8, pool_disable_warm_reuse=True)
    assert cfg.pool_max_warm_per_tuple == 8
    assert cfg.pool_disable_warm_reuse is True
