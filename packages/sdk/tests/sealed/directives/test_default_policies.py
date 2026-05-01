# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
from __future__ import annotations

from sagewai.sealed.directives.policies import (
    DirectivesConfig,
    default_alert_only_policies,
    seed_defaults_if_empty,
)


def test_default_alert_only_policies_returns_three():
    pols = default_alert_only_policies()
    assert {p.id for p in pols} == {
        "cost-overrun-default",
        "capability-gap-default",
        "rotation-drift-default",
    }
    for p in pols:
        assert p.action.kind == "alert_operator"
        assert p.requires_approval is False


def test_seed_defaults_seeds_when_empty():
    cfg = DirectivesConfig()  # no policies
    out = seed_defaults_if_empty(cfg)
    assert len(out.system_policies) == 3
    assert {p.id for p in out.system_policies} == {
        "cost-overrun-default",
        "capability-gap-default",
        "rotation-drift-default",
    }


def test_seed_defaults_does_not_override_existing():
    from sagewai.sealed.directives.models import PolicyAction

    existing = default_alert_only_policies()[0].model_copy(
        update={"action": PolicyAction(kind="abort_run", severity="critical")}
    )
    cfg = DirectivesConfig(system_policies=[existing])
    out = seed_defaults_if_empty(cfg)
    assert len(out.system_policies) == 1  # not replaced
    assert out.system_policies[0].action.kind == "abort_run"
